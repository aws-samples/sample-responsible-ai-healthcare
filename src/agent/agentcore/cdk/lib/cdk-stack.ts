// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import {
  AgentCoreApplication,
  AgentCoreMcp,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
} from '@aws/agentcore-cdk';
import { AspectPriority, Aspects, CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { InlinePolicyToManagedPolicy } from './aspects/inline-policy-to-managed-policy';
import { DiagnosticGateway, type PolicyEngineMode } from './diagnostic-gateway';
import {
  ASSESSED_INFERENCE_PROFILE_ID,
  RUNTIME_CONFIDENCE_THRESHOLD,
  RuntimeExecutionRole,
} from './runtime-execution-role';

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * The MCP specification containing gateways and servers.
   */
  mcpSpec?: AgentCoreMcpSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  /**
   * Bedrock guardrail every runtime must enforce (Terraform outputs guardrail_id / guardrail_version).
   * Omit only for synth-only tests with no runtimes.
   */
  guardrail?: { id: string; version: string };
  /**
   * Automated Reasoning policy the project guardrail attaches
   * (scripts/setup_guardrail.py prints AR_POLICY_ID / AR_POLICY_VERSION). The
   * runtime execution role must authorize bedrock:InvokeAutomatedReasoningPolicy
   * on this exact versioned policy ARN. Threaded through like guardrail so the
   * ARN carries no wildcard. Omit only for synth-only tests with no runtimes.
   */
  automatedReasoningPolicy?: { id: string; version: string };
  /**
   * Clinical guidelines knowledge base ID (docs/deployment.md, step 4). The agent
   * refuses to start without it. Omit only for synth-only tests with no runtimes.
   */
  knowledgeBaseId?: string;
  /**
   * Policy engine mode of the diagnostic gateway. ENFORCE (default) blocks requests the
   * guardrail policies deny; LOG_ONLY only logs the decisions, for threshold tuning.
   */
  gatewayPolicyMode?: PolicyEngineMode;
}

/**
 * CDK Stack that deploys AgentCore infrastructure.
 *
 * This is a thin wrapper that instantiates L3 constructs.
 * All resource logic and outputs are contained within the L3 constructs.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;
  /** Gateway in front of the diagnostic runtime (absent when the spec has no runtimes) */
  public readonly diagnosticGateway?: DiagnosticGateway;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { mcpSpec, credentials, guardrail, automatedReasoningPolicy, knowledgeBaseId } = props;

    // The agent refuses to start without GUARDRAIL_ID (src/agent/app/raihealthdiag/model/load.py).
    if (props.spec.runtimes.length > 0 && !guardrail) {
      throw new Error('A Bedrock guardrail is required for AgentCore runtimes (set GUARDRAIL_ID and GUARDRAIL_VERSION).');
    }
    // The agent refuses to start without KNOWLEDGE_BASE_ID (src/agent/app/raihealthdiag/main.py).
    if (props.spec.runtimes.length > 0 && !knowledgeBaseId) {
      throw new Error('A clinical guidelines knowledge base is required for AgentCore runtimes (set KNOWLEDGE_BASE_ID).');
    }

    // Least-privilege execution role shared by the runtimes. Passing it as
    // executionRoleArn stops the L3 construct from creating its default role,
    // which grants model access to every model and configuration-bundle write access.
    const runtimeRole =
      props.spec.runtimes.length > 0
        ? new RuntimeExecutionRole(this, 'RuntimeExecutionRole', {
            guardrail: guardrail!,
            automatedReasoningPolicy,
            knowledgeBaseId,
          })
        : undefined;

    // The policyEngines[] in agentcore.json are illustrative healthcare-RBAC Cedar
    // policies (Role::"Physician", ResourceType::"Patient", etc.). AgentCore's
    // Policy engine only accepts gateway/tool-authorization policies constrained to
    // the AgentCore::Gateway resource type and one statement per policy, so these
    // domain policies cannot be deployed as live PolicyEngine/Policy resources
    // without rewriting their meaning. They remain in the config and in
    // app/raihealthdiag/policies/*.cedar as the documented illustrative model; the
    // live RBAC control is IAM (infra/iam.tf), exercised by
    // scripts/06_test_access_control.py. Strip them from the deployed spec.
    const specWithoutPolicyEngines: AgentCoreProjectSpec = { ...props.spec, policyEngines: [] };
    const spec: AgentCoreProjectSpec = runtimeRole
      ? {
          ...specWithoutPolicyEngines,
          runtimes: specWithoutPolicyEngines.runtimes.map((r) => ({ ...r, executionRoleArn: runtimeRole.role.roleArn })),
        }
      : specWithoutPolicyEngines;

    // Create AgentCoreApplication with all agents
    this.application = new AgentCoreApplication(this, 'Application', {
      spec,
    });

    for (const environment of this.application.environments.values()) {
      const runtime = environment.runtime;
      runtime.addEnvironmentVariable('GUARDRAIL_ID', guardrail!.id);
      runtime.addEnvironmentVariable('GUARDRAIL_VERSION', guardrail!.version);
      runtime.addEnvironmentVariable('KNOWLEDGE_BASE_ID', knowledgeBaseId!);
      runtime.addEnvironmentVariable('BEDROCK_MODEL_ID', ASSESSED_INFERENCE_PROFILE_ID);
      // Passed explicitly so the deployed value is visible in the template instead
      // of relying on the agent's own default (main.py).
      runtime.addEnvironmentVariable('CONFIDENCE_THRESHOLD', RUNTIME_CONFIDENCE_THRESHOLD);
      // Keep patient data out of the telemetry. Prompts and model responses carry it,
      // and both default to being recorded: the AWS Distro for OpenTelemetry turns on
      // GenAI message capture when agent observability is enabled (os.environ.setdefault,
      // so this explicit value wins), and Strands records message content on its spans
      // unless OTEL_SEMCONV_STABILITY_OPT_IN carries gen_ai_unredacted_attributes. An
      // empty allowlist redacts every sensitive attribute; timings, token counts, model
      // and tool names are still recorded.
      runtime.addEnvironmentVariable('OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', 'false');
      runtime.addEnvironmentVariable('OTEL_SEMCONV_STABILITY_OPT_IN', 'gen_ai_unredacted_attributes=');
      // Imported roles get no automatic dependency; the runtime must not be
      // created before its permissions are attached.
      runtime.node.addDependency(runtimeRole!.role, ...runtimeRole!.policies);
      // Log access is scoped to this runtime's own log group (needs its runtime ID)
      runtimeRole!.grantRuntimeLogs(runtime.runtimeId);
    }

    // AgentCore Gateway with guardrails in policies in front of the runtime: the
    // request text is screened for prompt attacks, harmful content and direct
    // identifiers before it reaches the agent.
    const environments = [...this.application.environments.values()];
    if (environments.length > 1) {
      throw new Error('The diagnostic gateway fronts exactly one runtime; agentcore.json defines more.');
    }
    if (environments.length === 1) {
      this.diagnosticGateway = new DiagnosticGateway(this, 'DiagnosticGateway', {
        projectName: spec.name,
        runtimeArn: environments[0].runtime.runtimeArn,
        mode: props.gatewayPolicyMode,
      });
    }

    // Create AgentCoreMcp if there are gateways configured
    if (mcpSpec?.agentCoreGateways && mcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
    }

    // The AgentCore L3 constructs attach execution permissions as inline role
    // policies. Convert them to customer managed policies so the deployed
    // template satisfies iam-no-inline-policy-check / HIPAA 164.308(a)(4)
    // without a cdk-nag suppression. Runs at MUTATING priority so it completes
    // before read-only aspects (cdk-nag rule packs) inspect the tree.
    Aspects.of(this).add(new InlinePolicyToManagedPolicy(), {
      priority: AspectPriority.MUTATING,
    });

    // Stack-level output
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }
}
