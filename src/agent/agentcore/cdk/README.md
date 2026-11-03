# AgentCore CDK Project

This CDK project is managed by the AgentCore CLI. It deploys your agent infrastructure into AWS using the `@aws/agentcore-cdk` L3 constructs.

## Structure

- `bin/cdk.ts` — Entry point. Reads project configuration from `agentcore/` and creates a stack per deployment target.
- `lib/cdk-stack.ts` — Defines `AgentCoreStack`, which wraps the `AgentCoreApplication` L3 construct and adds the diagnostic gateway.
- `lib/diagnostic-gateway.ts` — AgentCore Gateway (IAM inbound), runtime target with an input schema, and a policy engine in `ENFORCE` mode whose Cedar policies apply guardrails in policies to every request.
- `lib/runtime-execution-role.ts` — Least-privilege execution role for the runtime.
- `test/` — Unit tests: stack synthesis (`cdk.test.ts`), runtime role (`runtime-role.test.ts`), gateway and guardrail policies (`gateway.test.ts`), cdk-nag (`cdk-nag.test.ts`).

## Useful commands

- `npm run build` compile TypeScript to JavaScript
- `npm run test` run unit tests
- `npx cdk synth` emit the synthesized CloudFormation template
- `npx cdk deploy` deploy this stack to your default AWS account/region
- `npx cdk diff` compare deployed stack with current state

## Usage

Deploy from the repository root with `make deploy-agent` (see [docs/deployment.md](../../../../docs/deployment.md), Step 6). Do not use `agentcore deploy`: the bundled CDK toolkit of `agentcore` CLI 0.13.1 cannot read this project's cloud assembly.
