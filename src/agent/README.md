This is an AgentCore CLI project (built and deployed with `@aws/agentcore-cdk` `0.1.0-alpha.33`
and the `agentcore` CLI `0.13.1`).

# Layout

The project root is `src/agent/`. It contains the agent code under `app/raihealthdiag/`, an `agentcore/`
folder holding the configuration and state (`agentcore.json`, `aws-targets.json`, `cdk/`, `policies/`), and
the Python packaging files (`pyproject.toml`, `requirements.txt`). The `agentcore` CLI commands (`deploy`,
`dev`, `invoke`) are run from this project root and read their configuration from `agentcore/`.

`agentcore/aws-targets.json` holds the deployment account and Region. It is **not tracked by git**
because it carries a real 12-digit account id; copy `agentcore/aws-targets.json.example` and
replace `123456789012` with your own account id before deploying.

## Agent code

The main entrypoint is `app/raihealthdiag/main.py`. Using the AgentCore SDK `@app.entrypoint` decorator, this
file defines a Starlette ASGI app with the Strands Agents SDK running within. `app/raihealthdiag/model/load.py`
instantiates the Bedrock model provider and enforces the model allowlist.

## Gateway guardrail policies (deployed)

The CDK stack deploys an AgentCore Gateway in front of the runtime with a policy engine in
`ENFORCE` mode (`agentcore/cdk/lib/diagnostic-gateway.ts`). Its Cedar policies use guardrails in
policies to deny requests whose text is a prompt attack, harmful content or carries direct
identifiers (HTTP 403) before the agent runs. They are CDK resources, not `policyEngines` entries
in `agentcore.json`. See [docs/responsible-ai.md](../../docs/responsible-ai.md#guardrails-at-the-agentcore-gateway).

## Access control: per-role Cedar RBAC policies are illustrative, not deployed

`agentcore.json` intentionally keeps **`policyEngines` empty**. The Cedar files under
`agentcore/policies/*.cedar` (Physician, Nurse, Admin, Auditor) are **illustrative RBAC artifacts only**.
They are NOT deployed as an AgentCore policy engine: `AWS::BedrockAgentCore::Policy` `CreatePolicy` rejects
them because AgentCore validates Cedar statements against a schema generated from an AgentCore Gateway's tool
input schemas and requires the policy resource to be constrained to an `AgentCore::Gateway` — which this
sample does not define. Re-adding populated `policyEngines` without a Gateway will break the deploy.
Access control in this sample is enforced by **IAM** (`infra/iam.tf`), verified by
`scripts/06_test_access_control.py`. Runtime Cedar enforcement is the documented production path (add an
AgentCore Gateway). See https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | Yes | Automated Reasoning guardrail printed by `scripts/setup_guardrail.py`; the agent refuses to start without it |
| `KNOWLEDGE_BASE_ID` | Yes | Clinical guidelines Knowledge Base (docs/deployment.md Step 4); `retrieve_clinical_guidelines` queries it |
| `BEDROCK_MODEL_ID` | No | Defaults to `us.anthropic.claude-sonnet-4-6` |
| `CONFIDENCE_THRESHOLD` | No | Fraction in `[0.50, 1.0]`; defaults to `0.70` |

# Installing dependencies

The agent has its own dependencies separate from the main project. Install them from this directory:

```bash
cd src/agent
pip install -e .
```

This installs `strands-agents`, `bedrock-agentcore`, and other runtime dependencies defined in `pyproject.toml`.

# Developing locally

If installation was successful, a virtual environment is already created with dependencies installed.

Run `source .venv/bin/activate` before developing.

`agentcore dev` will start a local server on 0.0.0.0:8080.

In a new terminal, you can send a prompt to that server with:

`agentcore dev "What can you do"`

# Deployment

Deploy from the repository root with `make deploy-agent` (the pinned CDK CLI; see docs/deployment.md Step 6 for why not `agentcore deploy`).

Use `agentcore invoke` to invoke your deployed agent.
