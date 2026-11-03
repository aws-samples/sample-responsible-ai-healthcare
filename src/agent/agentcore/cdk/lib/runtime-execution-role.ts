// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { Stack } from "aws-cdk-lib";
import {
  CfnManagedPolicy,
  ManagedPolicy,
  PolicyStatement,
  Role,
  ServicePrincipal,
} from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

/**
 * Destination Regions of the US cross-Region inference profile for Claude Sonnet 4.6
 * and of the us.guardrail.v1:0 guardrail profile.
 * https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html
 * https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-cross-region-support.html
 */
const US_INFERENCE_REGIONS = [
  "us-east-1",
  "us-east-2",
  "us-west-1",
  "us-west-2",
];

/** The model the bias/SHAP assessments were run against (see src/agent/model/load.py). */
export const ASSESSED_MODEL_ID = "anthropic.claude-sonnet-4-6";
export const ASSESSED_INFERENCE_PROFILE_ID = `us.${ASSESSED_MODEL_ID}`;

/**
 * Confidence threshold the deployed runtime enforces, as a FRACTION in the closed
 * interval [0.50, 1.0] — the scale src/agent/app/raihealthdiag/main.py accepts; it
 * raises RuntimeError at startup on anything outside it. A literal constant, not
 * process.env: a synth-time environment read is what let GUARDRAIL_ID differ
 * between the checked-in .env and the live runtime, and the deployed value has to
 * be auditable in the template.
 */
export const RUNTIME_CONFIDENCE_THRESHOLD = "0.70";

export interface RuntimeExecutionRoleProps {
  guardrail: { id: string; version: string };
  /**
   * Automated Reasoning policy attached to the project guardrail. When set, the
   * role authorizes bedrock:InvokeAutomatedReasoningPolicy on the EXACT versioned
   * policy ARN (arn:...:automated-reasoning-policy/<id>:<version>) — no wildcard,
   * so no AwsSolutions-IAM5 finding. When omitted (synth-only tests), the AR
   * statement is skipped. Values come from scripts/setup_guardrail.py output
   * (AR_POLICY_ID / AR_POLICY_VERSION), never hardcoded.
   */
  automatedReasoningPolicy?: { id: string; version: string };
  /**
   * Clinical guidelines knowledge base the agent retrieves from
   * (retrieve_clinical_guidelines in src/agent/app/raihealthdiag/main.py).
   * bedrock:Retrieve is authorized on this exact knowledge-base ARN:
   * https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-prereq-permissions-general.html
   * Omitted only in synth-only tests, which then skip the statement.
   */
  knowledgeBaseId?: string;
}

/**
 * Runtime log streams. ADOT structured logs go to "otel-rt-logs" and spans to
 * "spans" — both exact names, per
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-view.html
 *
 * stdout/stderr is the awkward one. AWS DOCUMENTS the stream as
 * "[runtime-logs] <UUID>"
 * (https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/view-observability-data-cloudwatch.html),
 * but the live service emits a dated, bracketed-id form:
 *   2026/10/12/[runtime-logs-6ba1dbd8-49dd-432f-9648-1bc191a2dd7a]efe9ea84-1ab3-4f07-95a7-41e1c276fc3e
 * (observed on this project's runtime in a logs:CreateLogStream AccessDenied,
 * CloudTrail eventID fbd2526f-4c98-467f-b18a-8d937a7baa5e, 2026-10-12T19:44:49Z)
 * — a YYYY/MM/DD/ prefix BEFORE the bracket and a runtime-generated id INSIDE
 * it, and the inner id is not always a dashed UUID (eventID
 * f28f197b-eae0-4900-929f-227e04a8f15a shows "[runtime-logs-c9a99d1f709f4491be3f6a7f559652876]").
 * The dated prefix is NOT VERIFIED against a doc describing it — the pattern
 * "*[runtime-logs*" is derived from the observed live stream name. A leading
 * wildcard is therefore required: "[runtime-logs]*" demanded that the name
 * BEGIN with the literal "[runtime-logs]", so it could never match and every
 * stdout/stderr stream was denied.
 */
const RUNTIME_LOG_STREAMS = ["otel-rt-logs", "spans", "*[runtime-logs*"];

/**
 * Least-privilege execution role for the AgentCore runtimes, replacing the role
 * the @aws/agentcore-cdk L3 construct would otherwise create. That default role
 * grants bedrock:InvokeModel on every foundation model and inference profile and
 * create/update/delete on every configuration bundle. This role follows the
 * documented direct-deploy (CodeZip) execution role, scoped to this project:
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
 *
 * Permissions are customer managed policies (no inline policies).
 */
export class RuntimeExecutionRole extends Construct {
  public readonly role: Role;
  /** Policies the runtime must wait for before it is created. */
  public readonly policies: ManagedPolicy[];
  private logsPolicyCount = 0;

  constructor(scope: Construct, id: string, props: RuntimeExecutionRoleProps) {
    super(scope, id);

    const stack = Stack.of(this);
    const { partition, region, account } = stack;

    // Trust policy with confused-deputy protection, per the AgentCore docs.
    this.role = new Role(this, "Role", {
      description: "AgentCore Runtime execution role (least privilege)",
      assumedBy: new ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": account },
          ArnLike: {
            "aws:SourceArn": `arn:${partition}:bedrock-agentcore:${region}:${account}:*`,
          },
        },
      }),
    });

    // Every resource below is an explicit ARN.
    const scoped = new ManagedPolicy(this, "ScopedPolicy", {
      description:
        "AgentCore runtime: assessed model, project guardrail, project runtime logs",
      roles: [this.role],
      statements: [
        new PolicyStatement({
          sid: "InvokeAssessedModel",
          actions: [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
          ],
          resources: [
            `arn:${partition}:bedrock:${region}:${account}:inference-profile/${ASSESSED_INFERENCE_PROFILE_ID}`,
            ...US_INFERENCE_REGIONS.map(
              (r) =>
                `arn:${partition}:bedrock:${r}::foundation-model/${ASSESSED_MODEL_ID}`,
            ),
          ],
        }),
        new PolicyStatement({
          // Required to invoke a model with a guardrail; the profile ARNs cover
          // the cross-Region guardrail created by scripts/setup_guardrail.py.
          sid: "ApplyProjectGuardrail",
          actions: ["bedrock:ApplyGuardrail"],
          resources: [
            `arn:${partition}:bedrock:${region}:${account}:guardrail/${props.guardrail.id}`,
            ...US_INFERENCE_REGIONS.map(
              (r) =>
                `arn:${partition}:bedrock:${r}:${account}:guardrail-profile/us.guardrail.v1:0`,
            ),
          ],
        }),
        // The project guardrail (scripts/setup_guardrail.py) attaches an
        // Automated Reasoning policy. When the guardrail runs during
        // ConverseStream, Bedrock calls InvokeAutomatedReasoningPolicy on the
        // attached policy version; without this the runtime fails with
        // AccessDenied on the AR policy ARN (that denial is itself proof the AR
        // check is being applied). Scoped to the EXACT versioned policy ARN
        // threaded through config (AR_POLICY_ID / AR_POLICY_VERSION) — no
        // wildcard, so no AwsSolutions-IAM5 finding. This mirrors the existing
        // GUARDRAIL_ID / GUARDRAIL_VERSION pin and accepts the same
        // version-increment brittleness by design.
        // SAR: InvokeAutomatedReasoningPolicy -> automated-reasoning-policy-version
        // https://servicereference.us-east-1.amazonaws.com/v1/bedrock/bedrock.json
        ...(props.knowledgeBaseId
          ? [
              new PolicyStatement({
                sid: "RetrieveClinicalGuidelines",
                actions: ["bedrock:Retrieve"],
                resources: [
                  `arn:${partition}:bedrock:${region}:${account}:knowledge-base/${props.knowledgeBaseId}`,
                ],
              }),
            ]
          : []),
        ...(props.automatedReasoningPolicy
          ? [
              new PolicyStatement({
                sid: "InvokeProjectAutomatedReasoning",
                actions: ["bedrock:InvokeAutomatedReasoningPolicy"],
                resources: [
                  `arn:${partition}:bedrock:${region}:${account}:automated-reasoning-policy/${props.automatedReasoningPolicy.id}:${props.automatedReasoningPolicy.version}`,
                ],
              }),
            ]
          : []),
      ],
    });

    // Metrics need no grant: the agent publishes them as CloudWatch embedded
    // metric format log lines into its own log group (main.py _publish_metrics).
    // logs:DescribeLogGroups was never used (IAM access advisor) and is not granted.
    //
    // Tracing is the one grant IAM can only express with "*": X-Ray actions have
    // no resource types (AWS Service Authorization Reference). Removing it was
    // measured live: agent traces stopped while Lambda traces continued, so it
    // stays. Only PutTraceSegments: the sampling and telemetry-record actions
    // were never used.
    const telemetry = new ManagedPolicy(this, "TelemetryPolicy", {
      description: "AgentCore runtime: X-Ray trace segments (no resource-level permissions exist)",
      roles: [this.role],
      statements: [
        new PolicyStatement({
          sid: "XRayTraceSegments",
          actions: ["xray:PutTraceSegments"],
          resources: ["*"],
        }),
      ],
    });

    suppressWildcards(telemetry, [
      {
        appliesTo: ["Resource::*"],
        reason:
          'xray:PutTraceSegments has no resource types in the AWS Service Authorization Reference, so it requires "*" ' +
          "(https://docs.aws.amazon.com/service-authorization/latest/reference/list_xray.html). Removing it was " +
          "measured live: the runtime's traces stopped. It is the only action in this policy.",
      },
    ]);

    this.policies = [scoped, telemetry];
  }

  /**
   * Grant write access to one runtime's log group, named
   * /aws/bedrock-agentcore/runtimes/<runtimeId>-<endpoint> (AgentCore observability docs).
   *
   * The ARN uses the runtime ID, which exists only after the runtime is
   * created, so this policy is attached after the runtime (it cannot be one of
   * the runtime's creation dependencies). Logs are written only when the
   * runtime is invoked, which happens after the stack completes.
   */
  public grantRuntimeLogs(
    runtimeId: string,
    endpointName = "DEFAULT",
  ): ManagedPolicy {
    const { partition, region, account } = Stack.of(this);
    const logGroup = `arn:${partition}:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/runtimes/${runtimeId}-${endpointName}`;
    const policy = new ManagedPolicy(
      this,
      `RuntimeLogsPolicy${this.logsPolicyCount++}`,
      {
        description: "AgentCore runtime: write to this runtime log group only",
        roles: [this.role],
        statements: [
          new PolicyStatement({
            sid: "RuntimeLogGroup",
            actions: [
              "logs:CreateLogGroup",
              "logs:DescribeLogStreams",
              "logs:PutResourcePolicy",
            ],
            resources: [logGroup],
          }),
          new PolicyStatement({
            // CreateLogStream and PutLogEvents are authorized on log-stream ARNs
            sid: "RuntimeLogStreams",
            actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
            resources: RUNTIME_LOG_STREAMS.map(
              (stream) => `${logGroup}:log-stream:${stream}`,
            ),
          }),
        ],
      },
    );
    suppressWildcards(policy, [
      {
        appliesTo: [
          { regex: "/^Resource::arn:.*:log-stream:\\*\\[runtime-logs\\*$/g" },
        ],
        reason:
          "The stdout/stderr stream name is generated by the runtime and carries BOTH a YYYY/MM/DD/ date prefix and " +
          "runtime-generated ids around and inside the bracket (observed live: " +
          '"2026/10/12/[runtime-logs-6ba1dbd8-...]efe9ea84-..." — CloudTrail eventID ' +
          "fbd2526f-4c98-467f-b18a-8d937a7baa5e, 2026-10-12T19:44:49Z), so wildcards on both sides of the literal are " +
          "unavoidable; otel-rt-logs and spans stay exact. The resource is still prefixed by THIS runtime's exact " +
          "log-group ARN (never log-group:*), making it strictly narrower than AWS's own documented AgentCore " +
          "execution role, which grants logs:CreateLogStream/PutLogEvents on " +
          "log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:* " +
          "(https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).",
      },
    ]);
    return policy;
  }
}

/**
 * Attach granular AwsSolutions-IAM5 suppressions as cdk-nag metadata on the
 * policy resource (avoids a runtime dependency on cdk-nag).
 */
export function suppressWildcards(
  policy: ManagedPolicy,
  entries: { appliesTo: (string | { regex: string })[]; reason: string }[],
): void {
  const cfn = policy.node.defaultChild as CfnManagedPolicy;
  cfn.addMetadata("cdk_nag", {
    rules_to_suppress: entries.map((e) => ({
      id: "AwsSolutions-IAM5",
      reason: e.reason,
      applies_to: e.appliesTo,
    })),
  });
}
