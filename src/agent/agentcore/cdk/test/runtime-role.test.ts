// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from "node:test";
import * as assert from "node:assert/strict";
import { AgentCoreRuntime } from "@aws/agentcore-cdk";
import * as cdk from "aws-cdk-lib";
import { AspectPriority, Aspects } from "aws-cdk-lib";
import { Asset } from "aws-cdk-lib/aws-s3-assets";
import { AwsSolutionsChecks, HIPAASecurityChecks } from "cdk-nag";
import * as path from "path";
import { RuntimeExecutionRole } from "../lib/runtime-execution-role";

/** Synthesizes the runtime exactly as AgentCoreStack wires it (mirrors src/agent/agentcore.json). */
function synthRuntime() {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, "RuntimeStack", {
    env: { account: "123456789012", region: "us-east-1" },
  });
  const role = new RuntimeExecutionRole(stack, "RuntimeExecutionRole", {
    guardrail: { id: "gr123", version: "1" },
    automatedReasoningPolicy: { id: "arpol123456", version: "4" },
    knowledgeBaseId: "KBTEST1234",
  });
  const runtime = new AgentCoreRuntime(stack, "Runtime", {
    projectName: "raihealthdiag",
    agent: {
      name: "raihealthdiag",
      build: "CodeZip",
      entrypoint: "main.py",
      codeLocation: "app/raihealthdiag/",
      runtimeVersion: "PYTHON_3_13",
      networkMode: "PUBLIC",
      protocol: "HTTP",
      executionRoleArn: role.role.roleArn,
    } as never,
    // Any directory works as the CodeZip asset for synthesis; use the agent's
    // real code location. Tests run compiled from agentcore/cdk/dist/test, so
    // src/agent is four levels up.
    codeAsset: new Asset(stack, "Code", {
      path: path.join(__dirname, "..", "..", "..", "..", "app", "raihealthdiag"),
    }),
  });
  runtime.node.addDependency(role.role, ...role.policies);
  role.grantRuntimeLogs(runtime.runtimeId);
  return { app, stack };
}

test("deployed runtime: least-privilege role, cdk-nag clean", () => {
  const { app, stack } = synthRuntime();
  Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }), {
    priority: AspectPriority.READONLY,
  });
  Aspects.of(app).add(new HIPAASecurityChecks({ verbose: true }), {
    priority: AspectPriority.READONLY,
  });

  const artifact = app.synth().getStackByName(stack.stackName);
  const resources = Object.values(
    (artifact.template as { Resources: Record<string, any> }).Resources,
  );
  const findings = artifact.messages.filter(
    (m) => m.level === "error" || m.level === "warning",
  );
  findings.forEach((f) =>
    console.log(`[${f.level}] ${f.id}: ${String(f.entry.data).slice(0, 300)}`),
  );

  // Only the project role exists; the L3 default role was not created.
  assert.equal(resources.filter((r) => r.Type === "AWS::IAM::Role").length, 1);
  assert.equal(resources.filter((r) => r.Type === "AWS::IAM::Policy").length, 0);

  // Model and guardrail access are explicit ARNs only.
  const statements = resources
    .filter((r) => r.Type === "AWS::IAM::ManagedPolicy")
    .flatMap((r) => r.Properties.PolicyDocument.Statement);
  for (const sid of ["InvokeAssessedModel", "ApplyProjectGuardrail"]) {
    const stmt = statements.find((s: { Sid: string }) => s.Sid === sid);
    assert.ok(!JSON.stringify(stmt.Resource).includes("*"));
  }
  assert.ok(!JSON.stringify(statements).includes("ConfigurationBundle"));

  // Logs: exact log group; only the stdout/stderr stream needs wildcards, because
  // the live name is "YYYY/MM/DD/[runtime-logs-<id>]<id>" (two wildcards: one for
  // the date prefix, one for the trailing ids). Never widened to log-group:*.
  const group = statements.find(
    (s: { Sid: string }) => s.Sid === "RuntimeLogGroup",
  );
  assert.ok(!JSON.stringify(group.Resource).includes("*"));
  assert.ok(JSON.stringify(group.Resource).includes("-DEFAULT"));
  const streams = JSON.stringify(
    statements.find((s: { Sid: string }) => s.Sid === "RuntimeLogStreams")
      .Resource,
  );
  assert.ok(streams.includes(":log-stream:otel-rt-logs"));
  assert.ok(streams.includes(":log-stream:spans"));
  assert.ok(streams.includes(":log-stream:*[runtime-logs*"));
  assert.ok(!streams.includes("log-group:*"));
  assert.equal(streams.match(/\*/g)?.length, 2);

  // The only Resource "*" grant is xray:PutTraceSegments, which has no resource
  // types. Metrics go out as EMF log lines, so there is no PutMetricData grant.
  for (const s of statements) {
    const actions = [].concat(s.Action);
    assert.equal(actions.some((a: string) => /PutMetricData|DescribeLogGroups|GetSampling|PutTelemetryRecords/.test(a)), false);
    if (s.Resource === "*") assert.deepEqual(actions, ["xray:PutTraceSegments"]);
  }

  assert.equal(findings.length, 0);
});
