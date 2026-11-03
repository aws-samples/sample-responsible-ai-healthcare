// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as cdk from "aws-cdk-lib";
import { AspectPriority, Aspects } from "aws-cdk-lib";
import { AwsSolutionsChecks, HIPAASecurityChecks } from "cdk-nag";
import {
  DIAGNOSTIC_ACTION,
  DiagnosticGateway,
  SCREENED_TEXT_PATHS,
  type PolicyEngineMode,
} from "../lib/diagnostic-gateway";

const ACCOUNT = "123456789012";
const RUNTIME_ARN = `arn:aws:bedrock-agentcore:us-east-1:${ACCOUNT}:runtime/raihealthdiag_raihealthdiag-AbCdEf1234`;

function synth(mode?: PolicyEngineMode, nag = false) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, "GatewayStack", {
    env: { account: ACCOUNT, region: "us-east-1" },
  });
  new DiagnosticGateway(stack, "DiagnosticGateway", {
    projectName: "raihealthdiag",
    runtimeArn: RUNTIME_ARN,
    mode,
  });
  if (nag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }), {
      priority: AspectPriority.READONLY,
    });
    Aspects.of(app).add(new HIPAASecurityChecks({ verbose: true }), {
      priority: AspectPriority.READONLY,
    });
  }
  const artifact = app.synth().getStackByName(stack.stackName);
  const resources = Object.values(
    (artifact.template as { Resources: Record<string, any> }).Resources,
  );
  const ofType = (type: string) => resources.filter((r) => r.Type === type);
  return { artifact, ofType };
}

function policyNamed(ofType: (t: string) => any[], name: string) {
  const policy = ofType("AWS::BedrockAgentCore::Policy").find(
    (p) => p.Properties.Name === name,
  );
  assert.ok(policy, `policy ${name} exists`);
  return policy;
}

/** Policy statements are Fn::Join'ed around the gateway ARN; flatten to text. */
function statementText(policy: any): string {
  // Guardrail functions need the AgentCore policy language definition, not plain Cedar
  assert.equal(policy.Properties.Definition.Cedar, undefined);
  return JSON.stringify(policy.Properties.Definition.Policy.Statement);
}

test("gateway: IAM inbound auth, policy engine enforced, runtime target with an input schema", () => {
  const { ofType } = synth();

  const [gateway] = ofType("AWS::BedrockAgentCore::Gateway");
  assert.equal(gateway.Properties.AuthorizerType, "AWS_IAM");
  assert.equal(gateway.Properties.ProtocolType, undefined);
  assert.equal(gateway.Properties.PolicyEngineConfiguration.Mode, "ENFORCE");

  const [target] = ofType("AWS::BedrockAgentCore::GatewayTarget");
  const runtime = target.Properties.TargetConfiguration.Http.AgentcoreRuntime;
  assert.equal(runtime.Arn, RUNTIME_ARN);
  assert.equal(runtime.Qualifier, "DEFAULT");
  const schema = JSON.parse(runtime.Schema.Source.InlinePayload);
  const body =
    schema.paths["/invocations"].post.requestBody.content["application/json"]
      .schema;
  assert.equal(body.properties.prompt.type, "string");
  assert.equal(
    body.properties.patient_data.properties.chief_complaint.type,
    "string",
  );
});

test("guardrails in policies: base permit for this account, three input guardrails", () => {
  const { ofType } = synth();
  const policies = ofType("AWS::BedrockAgentCore::Policy");
  assert.equal(policies.length, 4);
  for (const p of policies) {
    assert.equal(p.Properties.EnforcementMode, "ACTIVE");
    assert.ok(statementText(p).includes(DIAGNOSTIC_ACTION));
  }

  const permit = statementText(policyNamed(ofType, "PermitAccountCallers"));
  assert.ok(
    permit.startsWith('{"Fn::Join"') ||
      permit.includes("permit(principal is AgentCore::IamEntity"),
  );
  // Account anchored to its ARN position (no leading wildcard), for both caller forms
  assert.ok(
    permit.includes(`:iam::${ACCOUNT}:*`) &&
      permit.includes(`:sts::${ACCOUNT}:*`),
  );
  assert.ok(permit.includes('principal.id like \\"arn:'));
  assert.equal(
    permit.includes('like \\"*'),
    false,
    "no leading wildcard in the permit",
  );
  assert.equal(
    policyNamed(ofType, "PermitAccountCallers").Properties.ValidationMode,
    undefined,
  );

  const expectations: Record<string, string[]> = {
    ForbidPromptAttacks: [
      "BedrockGuardrails::PromptAttack",
      "JAILBREAK",
      "PROMPT_INJECTION",
      'greaterThan(decimal(\\"0.4\\"))',
    ],
    ForbidHarmfulContent: [
      "BedrockGuardrails::ContentFilter",
      "HATE",
      "INSULTS",
      "MISCONDUCT",
    ],
    ForbidDirectIdentifiers: [
      "BedrockGuardrails::SensitiveInformation",
      "US_SOCIAL_SECURITY_NUMBER",
      ".count() > 0",
    ],
  };
  for (const [name, fragments] of Object.entries(expectations)) {
    const policy = policyNamed(ofType, name);
    const text = statementText(policy);
    assert.ok(text.includes("forbid(principal"), `${name} is a forbid`);
    for (const f of fragments)
      assert.ok(text.includes(f), `${name} contains ${f}`);
    for (const p of SCREENED_TEXT_PATHS)
      assert.ok(text.includes(p.replace(/"/g, '\\"')), `${name} screens ${p}`);
    // A forbid gated only by a guardrail looks unconditional to policy validation
    assert.equal(policy.Properties.ValidationMode, "IGNORE_ALL_FINDINGS");
  }

  // Clinical text: these categories would block routine histories and eponyms
  const all = policies.map(statementText).join(" ");
  for (const excluded of [
    '\\"VIOLENCE\\"',
    '\\"SEXUAL\\"',
    '\\"NAME\\"',
    '\\"PHONE\\"',
    '\\"AGE\\"',
  ]) {
    assert.equal(all.includes(excluded), false, `${excluded} is not screened`);
  }
});

test("gateway role: least privilege, scoped trust, documented wildcards only", () => {
  const { ofType } = synth();
  const [role] = ofType("AWS::IAM::Role");
  const trust = role.Properties.AssumeRolePolicyDocument.Statement[0];
  assert.equal(trust.Principal.Service, "bedrock-agentcore.amazonaws.com");
  assert.equal(trust.Condition.StringEquals["aws:SourceAccount"], ACCOUNT);
  assert.ok(
    JSON.stringify(trust.Condition.ArnLike["aws:SourceArn"]).includes(
      "gateway/raihealthdiag-diagnostic-*",
    ),
  );

  assert.equal(ofType("AWS::IAM::Policy").length, 0);
  const statements = ofType("AWS::IAM::ManagedPolicy").flatMap(
    (p) => p.Properties.PolicyDocument.Statement,
  );
  const invoke = statements.find(
    (s: { Sid: string }) => s.Sid === "InvokeDiagnosticRuntime",
  );
  assert.deepEqual(invoke.Resource, [
    RUNTIME_ARN,
    `${RUNTIME_ARN}/runtime-endpoint/DEFAULT`,
  ]);

  // The only Resource "*" grant is bedrock:InvokeGuardrailChecks, which has no resource types
  for (const s of statements) {
    if (s.Resource === "*")
      assert.equal(s.Action, "bedrock:InvokeGuardrailChecks");
  }
  assert.equal(
    statements.filter((s: { Resource: unknown }) => s.Resource === "*").length,
    1,
  );
});

test("gateway: cdk-nag AwsSolutions and HIPAA packs report no findings", () => {
  const { artifact } = synth(undefined, true);
  const findings = artifact.messages.filter(
    (m) => m.level === "error" || m.level === "warning",
  );
  findings.forEach((f) =>
    console.log(`[${f.level}] ${f.id}: ${String(f.entry.data).slice(0, 300)}`),
  );
  assert.equal(findings.length, 0);
});

test("gateway: LOG_ONLY is passed through; other modes are refused", () => {
  const { ofType } = synth("LOG_ONLY");
  assert.equal(
    ofType("AWS::BedrockAgentCore::Gateway")[0].Properties
      .PolicyEngineConfiguration.Mode,
    "LOG_ONLY",
  );
  assert.throws(() => synth("OFF" as PolicyEngineMode), /ENFORCE or LOG_ONLY/);
});
