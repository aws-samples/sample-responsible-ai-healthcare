#!/usr/bin/env node
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { AgentCoreStack } from '../lib/cdk-stack';
import { ConfigIO, type AwsDeploymentTarget } from '@aws/agentcore-cdk';
import { App, type Environment } from 'aws-cdk-lib';
import * as path from 'path';
import * as fs from 'fs';

function toEnvironment(target: AwsDeploymentTarget): Environment {
  return {
    account: target.account,
    region: target.region,
  };
}

function sanitize(name: string): string {
  return name.replace(/_/g, '-');
}

function toStackName(projectName: string, targetName: string): string {
  return `AgentCore-${sanitize(projectName)}-${sanitize(targetName)}`;
}

async function main() {
  // Config root is parent of cdk/ directory. The CLI sets process.cwd() to agentcore/cdk/.
  const configRoot = path.resolve(process.cwd(), '..');
  const configIO = new ConfigIO({ baseDir: configRoot });

  let spec = await configIO.readProjectSpec();
  const targets = await configIO.readAWSDeploymentTargets();

  // Extract MCP configuration from project spec (gateway fields are typed on
  // AgentCoreProjectSpec in @aws/agentcore-cdk).
  const mcpSpec = spec.agentCoreGateways?.length
    ? {
        agentCoreGateways: spec.agentCoreGateways,
        mcpRuntimeTools: spec.mcpRuntimeTools,
        unassignedTargets: spec.unassignedTargets,
      }
    : undefined;

  // Read deployed state for credential ARNs (populated by pre-deploy identity setup)
  let deployedState: Record<string, unknown> | undefined;
  try {
    deployedState = JSON.parse(fs.readFileSync(path.join(configRoot, '.cli', 'deployed-state.json'), 'utf8'));
  } catch {
    // Deployed state may not exist on first deploy
  }

  if (targets.length === 0) {
    throw new Error('No deployment targets configured. Please define targets in agentcore/aws-targets.json');
  }

  // `make destroy-agent` sets AGENTCORE_TEARDOWN=1. cdk destroy deletes the stack by
  // name and never deploys this template, so it synthesizes the stack without runtimes
  // and the deploy-time requirements below do not apply: teardown works in a fresh
  // shell with none of the guardrail or knowledge base variables set.
  if (process.env.AGENTCORE_TEARDOWN === '1') {
    spec = { ...spec, runtimes: [] };
  }

  // Required: the agent refuses to start without a guardrail. Use the Automated
  // Reasoning guardrail printed by scripts/setup_guardrail.py.
  const guardrailId = process.env.GUARDRAIL_ID;
  const guardrailVersion = process.env.GUARDRAIL_VERSION;
  if (spec.runtimes.length > 0 && (!guardrailId || !guardrailVersion)) {
    throw new Error('GUARDRAIL_ID and GUARDRAIL_VERSION must be set to deploy AgentCore runtimes.');
  }
  const guardrail = guardrailId && guardrailVersion ? { id: guardrailId, version: guardrailVersion } : undefined;

  // Automated Reasoning policy attached to the project guardrail. The runtime
  // role needs bedrock:InvokeAutomatedReasoningPolicy on this exact versioned
  // ARN. Values come from scripts/setup_guardrail.py output (never hardcoded).
  // Required when runtimes exist because the project guardrail enforces an AR
  // policy; without the grant the runtime gets AccessDenied on the AR policy.
  const arPolicyId = process.env.AR_POLICY_ID;
  const arPolicyVersion = process.env.AR_POLICY_VERSION;
  if (spec.runtimes.length > 0 && (!arPolicyId || !arPolicyVersion)) {
    throw new Error('AR_POLICY_ID and AR_POLICY_VERSION must be set to deploy AgentCore runtimes (printed by scripts/setup_guardrail.py).');
  }
  const automatedReasoningPolicy =
    arPolicyId && arPolicyVersion ? { id: arPolicyId, version: arPolicyVersion } : undefined;

  // Clinical guidelines knowledge base the agent grounds its recommendations in
  // (docs/deployment.md, step 4). Never hardcoded.
  const knowledgeBaseId = process.env.KNOWLEDGE_BASE_ID;
  if (spec.runtimes.length > 0 && !knowledgeBaseId) {
    throw new Error('KNOWLEDGE_BASE_ID must be set to deploy AgentCore runtimes (docs/deployment.md, step 4).');
  }

  // Gateway policy engine mode: ENFORCE unless explicitly set to LOG_ONLY for tuning.
  const gatewayPolicyMode = process.env.GATEWAY_POLICY_MODE ?? 'ENFORCE';
  if (gatewayPolicyMode !== 'ENFORCE' && gatewayPolicyMode !== 'LOG_ONLY') {
    throw new Error('GATEWAY_POLICY_MODE must be ENFORCE or LOG_ONLY.');
  }

  const app = new App();

  for (const target of targets) {
    const env = toEnvironment(target);
    const stackName = toStackName(spec.name, target.name);

    // Extract credentials from deployed state for this target
    const targetState = (deployedState as Record<string, unknown>)?.targets as
      | Record<string, Record<string, unknown>>
      | undefined;
    const targetResources = targetState?.[target.name]?.resources as Record<string, unknown> | undefined;
    const credentials = targetResources?.credentials as
      | Record<string, { credentialProviderArn: string; clientSecretArn?: string }>
      | undefined;

    new AgentCoreStack(app, stackName, {
      spec,
      mcpSpec,
      credentials,
      guardrail,
      automatedReasoningPolicy,
      knowledgeBaseId,
      gatewayPolicyMode,
      env,
      description: `AgentCore stack for ${spec.name} deployed to ${target.name} (${target.region})`,
      tags: {
        'agentcore:project-name': spec.name,
        'agentcore:target-name': target.name,
      },
    });
  }

  app.synth();
}

main().catch((error: unknown) => {
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
