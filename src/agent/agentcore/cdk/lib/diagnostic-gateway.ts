// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { CfnOutput, Stack } from "aws-cdk-lib";
import {
  CfnGateway,
  CfnGatewayTarget,
  CfnPolicy,
  CfnPolicyEngine,
} from "aws-cdk-lib/aws-bedrockagentcore";
import {
  ManagedPolicy,
  PolicyStatement,
  Role,
  ServicePrincipal,
} from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { suppressWildcards } from "./runtime-execution-role";

/**
 * Target name. With an AgentCore Runtime target the gateway exposes one Cedar
 * action, <TargetName>___POST:/invocations.
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html
 */
export const DIAGNOSTIC_TARGET_NAME = "diagnostic";
export const DIAGNOSTIC_ACTION = `${DIAGNOSTIC_TARGET_NAME}___POST:/invocations`;

/**
 * Free-text fields of the diagnostic request (src/agent/app/raihealthdiag/main.py,
 * invoke). Numeric fields (age, vital signs, labs) carry no text to screen. Each
 * path is sent to the guardrail as its own content block; a path that matches
 * nothing contributes nothing, and if none matches the request is denied.
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/example-policies-guardrails.html
 */
export const SCREENED_TEXT_PATHS = [
  "$.prompt",
  "$.patient_data.chief_complaint",
  "$.patient_data.ecg",
  "$.patient_data.medical_history[*]",
  "$.patient_data.medications[*]",
];

/**
 * Input schema of the runtime target. An HTTP Runtime target needs a schema before
 * the policy engine can apply guardrails to its request fields.
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html
 */
export const DIAGNOSTIC_TARGET_SCHEMA = JSON.stringify({
  openapi: "3.0.0",
  info: { title: "Healthcare diagnostic agent invocation", version: "1.0.0" },
  paths: {
    "/invocations": {
      post: {
        operationId: "invokeDiagnosticAgent",
        requestBody: {
          required: true,
          content: {
            "application/json": {
              schema: {
                type: "object",
                properties: {
                  prompt: { type: "string" },
                  patient_data: {
                    type: "object",
                    properties: {
                      age: { type: "number" },
                      gender: { type: "string" },
                      chief_complaint: { type: "string" },
                      vital_signs: { type: "object" },
                      lab_results: { type: "object" },
                      ecg: { type: "string" },
                      medical_history: {
                        type: "array",
                        items: { type: "string" },
                      },
                      medications: { type: "array", items: { type: "string" } },
                    },
                  },
                },
              },
            },
          },
        },
        responses: { "200": { description: "Diagnostic recommendation" } },
      },
    },
  },
});

/**
 * Guardrail thresholds. Content filter and prompt attack scores are discrete
 * (0, 0.2, ... 1.0); greaterThan(0.4) fires at 0.6 and above. Prompt attack uses the
 * documented default (0.4). The content filter threshold is set above its 0.2
 * default as a precaution, because clinical histories (substance use, self-harm
 * screening) can resemble misconduct; tune both with LOG_ONLY and the evaluation
 * set (docs/responsible-ai.md).
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html#thresholds-for-guardrails
 */
export const PROMPT_ATTACK_THRESHOLD = "0.4";
export const CONTENT_FILTER_THRESHOLD = "0.6";

export type PolicyEngineMode = "ENFORCE" | "LOG_ONLY";

export interface DiagnosticGatewayProps {
  /** Lowercase project name; the gateway is named <projectName>-diagnostic. */
  projectName: string;
  /** ARN of the diagnostic AgentCore Runtime. */
  runtimeArn: string;
  /** ENFORCE (default) blocks; LOG_ONLY evaluates without blocking, for threshold tuning only. */
  mode?: PolicyEngineMode;
}

const GATEWAY_NAME_PATTERN = /^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$/;

/**
 * AgentCore Gateway with a Policy in AgentCore engine in front of the diagnostic
 * runtime. Callers authenticate with IAM (SigV4) and need
 * bedrock-agentcore:InvokeGateway. Cedar policies permit callers from this account
 * and use guardrails in policies to deny requests whose text is a prompt attack,
 * harmful content or carries direct identifiers, before the request reaches the
 * agent. The agent's own Bedrock guardrail (Automated Reasoning, content filters)
 * stays the second layer.
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html
 */
export class DiagnosticGateway extends Construct {
  public readonly gateway: CfnGateway;
  public readonly role: Role;
  public readonly policyEngine: CfnPolicyEngine;
  public readonly target: CfnGatewayTarget;
  public readonly policies: CfnPolicy[] = [];

  constructor(scope: Construct, id: string, props: DiagnosticGatewayProps) {
    super(scope, id);
    const { partition, region, account } = Stack.of(this);

    if (!GATEWAY_NAME_PATTERN.test(props.projectName)) {
      throw new Error(
        `projectName "${props.projectName}" must be lowercase letters, digits and hyphens.`,
      );
    }
    const mode = props.mode ?? "ENFORCE";
    if (mode !== "ENFORCE" && mode !== "LOG_ONLY") {
      throw new Error("Policy engine mode must be ENFORCE or LOG_ONLY.");
    }

    const gatewayName = `${props.projectName}-diagnostic`;
    // Gateway IDs start with the gateway name. Used where the exact ARN cannot exist
    // yet: the trust condition and policy evaluation during gateway creation.
    // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html
    const gatewayArnPattern = `arn:${partition}:bedrock-agentcore:${region}:${account}:gateway/${gatewayName}-*`;

    this.policyEngine = new CfnPolicyEngine(this, "PolicyEngine", {
      name: `${props.projectName.replace(/-/g, "_")}_diagnostic_policies`,
      description:
        "Authorization and guardrails for the diagnostic agent gateway",
    });

    // Trust only an AgentCore gateway named for this project, in this account.
    this.role = new Role(this, "Role", {
      description: "AgentCore Gateway execution role for the diagnostic agent",
      assumedBy: new ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": account },
          ArnLike: { "aws:SourceArn": gatewayArnPattern },
        },
      }),
    });

    const scoped = new ManagedPolicy(this, "ScopedPolicy", {
      description:
        "AgentCore Gateway: invoke the diagnostic runtime and evaluate its policy engine",
      roles: [this.role],
      statements: [
        new PolicyStatement({
          sid: "InvokeDiagnosticRuntime",
          actions: ["bedrock-agentcore:InvokeAgentRuntime"],
          resources: [
            props.runtimeArn,
            `${props.runtimeArn}/runtime-endpoint/DEFAULT`,
          ],
        }),
        // Gateway creation checks AuthorizeAction on the policy engine and on the new
        // gateway's own ARN, whose ID is generated during creation, so the gateway is
        // named by this project's gateway-name pattern (the one the trust policy uses).
        // The docs allow a gateway pattern here.
        new PolicyStatement({
          sid: "EvaluatePolicies",
          actions: [
            "bedrock-agentcore:AuthorizeAction",
            "bedrock-agentcore:PartiallyAuthorizeActions",
          ],
          resources: [this.policyEngine.attrPolicyEngineArn, gatewayArnPattern],
        }),
        new PolicyStatement({
          sid: "ReadPolicyEngine",
          actions: ["bedrock-agentcore:GetPolicyEngine"],
          resources: [this.policyEngine.attrPolicyEngineArn],
        }),
      ],
    });

    // SECURITY EXCEPTION: policy evaluation is granted on this project's gateway-name
    // pattern because the gateway ARN does not exist until creation - Approved by the repository owner on 2026-10-31
    suppressWildcards(scoped, [
      {
        appliesTo: [
          `Resource::arn:<AWS::Partition>:bedrock-agentcore:${region}:${account}:gateway/${gatewayName}-*`,
        ],
        reason:
          "CreateGateway authorizes AuthorizeAction on the new gateway's own ARN, whose ID is generated during creation, " +
          "so the exact ARN cannot be granted beforehand. The pattern is limited to this project's gateway name in this " +
          "account and Region, and the same pattern scopes the role's trust policy " +
          "(https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html).",
      },
    ]);

    // Policy in AgentCore calls Bedrock Guardrails with forward access session
    // credentials from this role.
    // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html#policy-guardrails-before-you-begin
    const guardrailChecks = new ManagedPolicy(this, "GuardrailChecksPolicy", {
      description:
        "AgentCore Gateway: guardrail checks in policies (no resource-level permissions exist)",
      roles: [this.role],
      statements: [
        new PolicyStatement({
          sid: "InvokeGuardrailChecks",
          actions: ["bedrock:InvokeGuardrailChecks"],
          resources: ["*"],
        }),
      ],
    });
    // SECURITY EXCEPTION: bedrock:InvokeGuardrailChecks has no resource types in the
    // AWS Service Authorization Reference, so it requires "*" - Approved by the repository owner on 2026-10-31
    suppressWildcards(guardrailChecks, [
      {
        appliesTo: ["Resource::*"],
        reason:
          'bedrock:InvokeGuardrailChecks has no resource types in the AWS Service Authorization Reference, so it requires "*" ' +
          "(https://servicereference.us-east-1.amazonaws.com/v1/bedrock/bedrock.json). It is the only action in this policy, " +
          "and the checks evaluate inline content only.",
      },
    ]);

    this.gateway = new CfnGateway(this, "Gateway", {
      name: gatewayName,
      description: "Governed entry point to the diagnostic agent",
      roleArn: this.role.roleArn,
      // No protocol type: required for AgentCore Runtime targets
      // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html
      authorizerType: "AWS_IAM",
      policyEngineConfiguration: {
        arn: this.policyEngine.attrPolicyEngineArn,
        mode,
      },
    });
    this.gateway.node.addDependency(scoped, guardrailChecks);

    this.target = new CfnGatewayTarget(this, "DiagnosticTarget", {
      gatewayIdentifier: this.gateway.attrGatewayIdentifier,
      name: DIAGNOSTIC_TARGET_NAME,
      description: "Diagnostic AgentCore Runtime",
      targetConfiguration: {
        http: {
          agentcoreRuntime: {
            arn: props.runtimeArn,
            qualifier: "DEFAULT",
            schema: { source: { inlinePayload: DIAGNOSTIC_TARGET_SCHEMA } },
          },
        },
      },
      credentialProviderConfigurations: [
        { credentialProviderType: "GATEWAY_IAM_ROLE" },
      ],
    });

    const resource = `resource == AgentCore::Gateway::"${this.gateway.attrGatewayArn}"`;
    const action = `action == AgentCore::Action::"${DIAGNOSTIC_ACTION}"`;
    const paths = `[${SCREENED_TEXT_PATHS.map((p) => `"${p}"`).join(", ")}]`;
    const statements = [
      {
        name: "PermitAccountCallers",
        description:
          "IAM callers from this account may request a diagnosis (the base permit the guardrails narrow)",
        // Anchored to the account segment of the two ARN forms an IAM caller has
        // (the gateway strips the session name): arn:<partition>:iam::<account>:...
        // for users and roles, arn:<partition>:sts::<account>:assumed-role/... for sessions.
        // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/example-policies-principal.html
        statement:
          `permit(principal is AgentCore::IamEntity, ${action}, ${resource}) when { ` +
          `principal.id like "arn:${partition}:iam::${account}:*" || principal.id like "arn:${partition}:sts::${account}:*" };`,
      },
      {
        name: "ForbidPromptAttacks",
        description:
          "Deny requests whose text is a jailbreak or prompt injection attempt",
        statement:
          `forbid(principal, ${action}, ${resource}) when guardrails { ` +
          `BedrockGuardrails::PromptAttack(["JAILBREAK", "PROMPT_INJECTION"], ${paths})` +
          `.maxConfidenceScore().greaterThan(decimal("${PROMPT_ATTACK_THRESHOLD}")) };`,
        guardrail: true,
      },
      {
        // VIOLENCE and SEXUAL are not screened here: injuries, assault and sexual
        // health are routine clinical history. The agent's Bedrock guardrail still
        // applies its content filters to the model call.
        name: "ForbidHarmfulContent",
        description: "Deny requests containing hate, insults or misconduct",
        statement:
          `forbid(principal, ${action}, ${resource}) when guardrails { ` +
          `BedrockGuardrails::ContentFilter(["HATE", "INSULTS", "MISCONDUCT"], ${paths})` +
          `.maxConfidenceScore().greaterThan(decimal("${CONTENT_FILTER_THRESHOLD}")) };`,
        guardrail: true,
      },
      {
        // Minimum necessary: a diagnosis needs none of these. NAME and PHONE are not
        // screened: eponyms ("Beck triad") and "call 911" are clinical text.
        name: "ForbidDirectIdentifiers",
        description:
          "Deny requests carrying direct identifiers the diagnosis does not need",
        statement:
          `forbid(principal, ${action}, ${resource}) when guardrails { ` +
          `BedrockGuardrails::SensitiveInformation(["US_SOCIAL_SECURITY_NUMBER", "EMAIL", "US_PASSPORT_NUMBER", "CREDIT_DEBIT_CARD_NUMBER"], ${paths})` +
          `.count() > 0 };`,
        guardrail: true,
      },
    ];
    for (const s of statements) {
      const policy = new CfnPolicy(this, s.name, {
        policyEngineId: this.policyEngine.attrPolicyEngineId,
        name: s.name,
        description: s.description,
        // The `policy` definition is the AgentCore policy language, which adds guardrail
        // functions (BedrockGuardrails::...) to Cedar; a `cedar` definition is parsed as
        // plain Cedar and rejects them ("unexpected token `guardrails`", measured on deploy).
        // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html#defining-guardrails-in-policies
        definition: { policy: { statement: s.statement } },
        enforcementMode: "ACTIVE",
        // Policy validation cannot interpret a guardrail condition, so a forbid gated
        // only by a guardrail is reported as Overly Restrictive. The base permit above
        // keeps the action reachable.
        // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-guardrails-in-policies.html#defining-guardrails-in-policies
        ...(s.guardrail ? { validationMode: "IGNORE_ALL_FINDINGS" } : {}),
      });
      policy.node.addDependency(this.target);
      this.policies.push(policy);
    }

    // URL format from https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html
    new CfnOutput(this, "GatewayInvocationUrl", {
      description:
        "POST target for diagnostic requests (SigV4, service bedrock-agentcore)",
      value: `https://${this.gateway.attrGatewayIdentifier}.gateway.bedrock-agentcore.${region}.amazonaws.com/${DIAGNOSTIC_TARGET_NAME}/invocations`,
    });
    new CfnOutput(this, "GatewayId", {
      value: this.gateway.attrGatewayIdentifier,
    });
    new CfnOutput(this, "PolicyEngineId", {
      value: this.policyEngine.attrPolicyEngineId,
    });
  }
}
