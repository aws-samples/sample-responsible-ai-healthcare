// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/cdk-stack';
import { ASSESSED_INFERENCE_PROFILE_ID, RUNTIME_CONFIDENCE_THRESHOLD } from '../lib/runtime-execution-role';

test('AgentCoreStack synthesizes with empty spec', () => {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec: {
      name: 'testproject',
      version: 1,
      managedBy: 'CDK' as const,
      runtimes: [],
      memories: [],
      credentials: [],
      evaluators: [],
      onlineEvalConfigs: [],
      configBundles: [],
      policyEngines: [],
      agentCoreGateways: [],
      mcpRuntimeTools: [],
      unassignedTargets: [],
    },
  });
  const template = Template.fromStack(stack);
  template.hasOutput('StackNameOutput', {
    Description: 'Name of the CloudFormation Stack',
  });
});

/**
 * The runtime env is the contract between the stack and the agent code. The agent
 * reads CONFIDENCE_THRESHOLD as a FRACTION at import time and refuses to start
 * outside [0.50, 1.0] (src/agent/app/raihealthdiag/main.py), so the deployed value
 * has to be pinned here rather than left to the agent's default.
 * Mirrors src/agent/agentcore/agentcore.json.
 */
test('the synthesized runtime env pins the confidence threshold as a fraction', () => {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'RuntimeEnvStack', {
    env: { account: '123456789012', region: 'us-east-1' },
    guardrail: { id: 'gr123', version: '1' },
    automatedReasoningPolicy: { id: 'arpol123456', version: '4' },
    knowledgeBaseId: 'KBTEST1234',
    spec: {
      name: 'raihealthdiag',
      version: 1,
      managedBy: 'CDK' as const,
      runtimes: [
        {
          name: 'raihealthdiag',
          build: 'CodeZip',
          entrypoint: 'main.py',
          codeLocation: 'app/raihealthdiag/',
          runtimeVersion: 'PYTHON_3_13',
          networkMode: 'PUBLIC',
          protocol: 'HTTP',
        },
      ] as never,
      memories: [],
      credentials: [],
      evaluators: [],
      onlineEvalConfigs: [],
      configBundles: [],
      policyEngines: [],
      agentCoreGateways: [],
      mcpRuntimeTools: [],
      unassignedTargets: [],
    },
  });

  Template.fromStack(stack).hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
    EnvironmentVariables: {
      GUARDRAIL_ID: 'gr123',
      GUARDRAIL_VERSION: '1',
      KNOWLEDGE_BASE_ID: 'KBTEST1234',
      BEDROCK_MODEL_ID: ASSESSED_INFERENCE_PROFILE_ID,
      CONFIDENCE_THRESHOLD: '0.70',
      // Patient data stays out of traces and OTel logs.
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: 'false',
      OTEL_SEMCONV_STABILITY_OPT_IN: 'gen_ai_unredacted_attributes=',
    },
  });
  assert.equal(RUNTIME_CONFIDENCE_THRESHOLD, '0.70');
});

/** Retrieval is granted on exactly one knowledge base, and the stack refuses to synthesize an ungrounded runtime. */
test('the runtime may retrieve only from the clinical guidelines knowledge base', () => {
  const spec = {
    name: 'raihealthdiag',
    version: 1,
    managedBy: 'CDK' as const,
    runtimes: [
      {
        name: 'raihealthdiag',
        build: 'CodeZip',
        entrypoint: 'main.py',
        codeLocation: 'app/raihealthdiag/',
        runtimeVersion: 'PYTHON_3_13',
        networkMode: 'PUBLIC',
        protocol: 'HTTP',
      },
    ] as never,
    memories: [],
    credentials: [],
    evaluators: [],
    onlineEvalConfigs: [],
    configBundles: [],
    policyEngines: [],
    agentCoreGateways: [],
    mcpRuntimeTools: [],
    unassignedTargets: [],
  };
  const base = {
    env: { account: '123456789012', region: 'us-east-1' },
    guardrail: { id: 'gr123', version: '1' },
    automatedReasoningPolicy: { id: 'arpol123456', version: '4' },
    spec,
  };
  const stack = new AgentCoreStack(new cdk.App(), 'KbStack', { ...base, knowledgeBaseId: 'KBTEST1234' });
  const policies = Template.fromStack(stack).findResources('AWS::IAM::ManagedPolicy');
  const retrieve = Object.values(policies)
    .flatMap((p) => (p as { Properties: { PolicyDocument: { Statement: Record<string, unknown>[] } } }).Properties.PolicyDocument.Statement)
    .filter((s) => JSON.stringify(s.Action).includes('bedrock:Retrieve'));
  assert.equal(retrieve.length, 1);
  assert.ok(JSON.stringify(retrieve[0].Resource).includes(':knowledge-base/KBTEST1234'));
  assert.ok(!JSON.stringify(retrieve[0].Resource).includes('*'));

  assert.throws(() => new AgentCoreStack(new cdk.App(), 'NoKbStack', base), /KNOWLEDGE_BASE_ID/);
});
