# Cleanup Guide

Follow these steps to remove all resources created by this demo and stop incurring AWS charges.

## The short version

```bash
make full-cleanup
```

That target runs the whole teardown in the correct order — application runtime before
infrastructure — and is equivalent to:

```bash
python scripts/cleanup.py --phase pre-clean     # remove the blockers that stall terraform destroy
make destroy-agent                              # cdk destroy for the AgentCore runtime
make destroy                                    # terraform destroy
#   on failure: python scripts/cleanup.py --phase unblock --force, then make destroy again
python scripts/cleanup.py --phase post-clean    # guardrails, Knowledge Base, demo log groups
python scripts/cleanup.py --phase verify        # report anything still standing
```

Add `CLEANUP_FLAGS=--force` to skip the confirmation prompt in CI:

```bash
make full-cleanup CLEANUP_FLAGS=--force
```

### How the work is split

The CLI steps live in the `Makefile`; `scripts/cleanup.py` only talks to AWS APIs through boto3 and
never shells out to another program. The script's phases:

| Phase | What it removes | Safe to run on its own |
|-------|-----------------|------------------------|
| `pre-clean` | Orphaned SageMaker endpoints/configs/models, running `rai-healthcare-demo-bias-*` Processing jobs and `rai-healthcare-demo-eval-*` evaluation jobs (stopped), the MLflow App `rai-healthcare-demo-mlflow`, all object versions in the demo buckets | Yes |
| `unblock` | EFS mount targets and filesystems left by a SageMaker Studio domain from an earlier version of this sample, non-default security groups in the project VPC | Only after a failed `terraform destroy` |
| `post-clean` | Bedrock guardrails, the Knowledge Base and its data sources (switched to `dataDeletionPolicy` RETAIN first), then its OpenSearch Serverless collection and the `rai-healthcare-demo-enc`/`-net`/`-access` policies, finished `rai-healthcare-demo-eval-*` Bedrock evaluation jobs (25 per `BatchDeleteEvaluationJob` call), demo CloudWatch log groups | Yes |
| `verify` | Nothing — reports what remains | Yes, read-only |

The MLflow App is deleted in `pre-clean`, before `terraform destroy` removes its role, artifact
bucket and KMS key. SageMaker AI Processing jobs cannot be deleted, only stopped; their records age
out on their own.

```bash
python scripts/cleanup.py                 # pre-clean + post-clean + verify
python scripts/cleanup.py --phase verify  # report only
```

Declining the confirmation prompt exits with code 2, so `make full-cleanup` stops instead of going
on to destroy infrastructure.

## Step 1: Destroy Terraform Resources

`make destroy` (or `cd infra && terraform destroy`) removes:
- VPC, subnets, VPC endpoints, AWS Network Firewall
- S3 buckets and their contents, including the cross-Region replicas in `replica_region`
- IAM roles and policies
- Lambda function and DLQ
- CloudWatch dashboard, alarms, and log groups
- SNS topic and subscriptions
- The IAM roles of the Processing job, the MLflow App and Bedrock Evaluations
- KMS keys, including the replica-Region key (scheduled for deletion)

Expected teardown time: 5-10 minutes.

**Before destroying, turn off the firewall's delete protection.** `aws_networkfirewall_firewall.main` in `infra/vpc.tf` sets `delete_protection = true`, so `terraform destroy` fails on the firewall while it is on. `make full-cleanup` does this for you: its `pre-clean` phase (`scripts/cleanup.py`) turns off delete protection on the firewall named `rai-healthcare-demo-egress` and nothing else. When running `terraform destroy` on its own, run `python scripts/cleanup.py --phase pre-clean` first, or set `delete_protection = false` and `terraform apply` before the destroy.

### Known Issue: Terraform Destroy Hangs on Subnet or VPC

This applies only to a deployment created before this sample removed its SageMaker Studio domain. Studio creates an EFS filesystem with mount targets in your private subnet. When you destroy the SageMaker domain, the EFS mount target ENI takes 10-15 minutes to fully release. During this time, `terraform destroy` will show:

```
aws_subnet.private_a: Still destroying... [10m00s elapsed]
```

**Solution: Wait.** It will eventually complete (typically 10-12 minutes). Do NOT cancel it.

If you do cancel and need to retry, `python scripts/cleanup.py --phase unblock --force` does the
EFS and security-group cleanup below for you, and `make full-cleanup` already retries
`terraform destroy` once after running it. To do it by hand:

```bash
# 1. Find and delete the EFS mount target blocking the subnet
aws efs describe-file-systems --query 'FileSystems[?Name==null].FileSystemId' --output text
# For each filesystem ID:
aws efs describe-mount-targets --file-system-id <fs-id> --query 'MountTargets[].MountTargetId' --output text
aws efs delete-mount-target --mount-target-id <mt-id>

# 2. Wait 30 seconds for ENI release
sleep 30

# 3. Delete the EFS filesystem
aws efs delete-file-system --file-system-id <fs-id>

# 4. If VPC still won't delete (security group dependency):
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=<vpc-id>" --query 'SecurityGroups[?GroupName!=`default`].GroupId' --output text
# Delete each non-default security group:
aws ec2 delete-security-group --group-id <sg-id>

# 5. Retry destroy
terraform destroy
```

### Known Issue: S3 Bucket Not Empty (Versioned Objects)

If `terraform destroy` fails with `BucketNotEmpty`, the bucket has versioned objects:

```bash
# Empty all versions
aws s3api list-object-versions --bucket rai-healthcare-demo-<account-id>-us-east-1 \
  --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}, Markers: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
  --output json | python3 -c "
import json, sys, boto3
data = json.load(sys.stdin)
s3 = boto3.client('s3')
bucket = 'rai-healthcare-demo-<account-id>-us-east-1'
items = (data.get('Objects') or []) + (data.get('Markers') or [])
for i in range(0, len(items), 1000):
    s3.delete_objects(Bucket=bucket, Delete={'Objects': items[i:i+1000], 'Quiet': True})
print(f'Deleted {len(items)} versions')
"

# Then retry
terraform destroy
```

### Known Issue: State Lock

If a previous `terraform destroy` was interrupted, you may see:

```
Error: Error acquiring the state lock
```

Fix:

```bash
# Remove the lock file
rm -f .terraform.tfstate.lock.info
# Retry with lock disabled
terraform destroy -lock=false
```

## Step 2: Remove AgentCore Resources

> Destroy the agent runtime **before** the Terraform infrastructure it depends on.
> `make full-cleanup` already does this in the right order.

```bash
make destroy-agent
```

That is `cd src/agent/agentcore/cdk && npm run build && AGENTCORE_TEARDOWN=1 ./node_modules/.bin/cdk destroy --force`.
`AGENTCORE_TEARDOWN=1` lets the CDK app synthesize without the `GUARDRAIL_*`, `AR_POLICY_*` and
`KNOWLEDGE_BASE_ID` variables, so teardown works in a fresh shell.

This removes the CloudFormation stack `AgentCore-raihealthdiag-default`: the AgentCore runtime, its
execution role and policies, and the AgentCore Gateway with its target, policy engine, Cedar
policies and execution role (the per-role Cedar RBAC policies are illustrative; see
[responsible-ai.md](responsible-ai.md)).

## Step 3: Manual Cleanup

Some resources may require manual deletion if they were created outside Terraform or if Terraform destroy fails partially.

### Known leftovers that `terraform destroy` will not remove

Two classes of leftover have been observed on this project and neither self-resolves. Check for
both after a destroy.

**1. An orphan log bucket not present in Terraform state.** The audit and server-access-log buckets
use `bucket_prefix`, so each `terraform apply` against a fresh state produces a new generated name
(`<project>-logs-<generated-suffix>`, `<project>-s3al-<generated-suffix>`). If state is ever
recreated or discarded, the previously created buckets stay behind, are no longer tracked, and
`terraform destroy` does not see them. They are small — tens of objects, well under a megabyte —
but they are audit-log buckets, so delete them deliberately rather than leaving them:

```bash
# Every log bucket that exists for this project
aws s3api list-buckets \
  --query "Buckets[?contains(Name,'rai-healthcare-demo-logs-') || contains(Name,'rai-healthcare-demo-s3al-')].Name" \
  --output text | tr '\t' '\n' | sort > /tmp/log-buckets-live.txt

# Every log bucket Terraform still tracks
terraform -chdir=infra state pull \
  | grep -oE 'rai-healthcare-demo-(logs|s3al)[a-z0-9-]*' | sort -u > /tmp/log-buckets-state.txt

# Anything live but untracked is an orphan
comm -23 /tmp/log-buckets-live.txt /tmp/log-buckets-state.txt
```

Empty and remove each orphan deliberately:

```bash
aws s3 rm "s3://<orphan-bucket>" --recursive
aws s3api delete-bucket --bucket "<orphan-bucket>"
```

If the bucket has versioning enabled, use the version-deletion snippet from "Known Issue: S3 Bucket
Not Empty" above before `delete-bucket`.

**2. A Knowledge Base stuck in `DELETE_UNSUCCESSFUL`.** Terraform owns only the Knowledge Base's
IAM role and the guideline objects in S3 (`infra/knowledge_base.tf`) — the Knowledge Base and its
OpenSearch Serverless collection are created outside Terraform, so `terraform destroy` never
deletes them. Deleting the collection first leaves the Knowledge Base and its data source in
`DELETE_UNSUCCESSFUL`, pointing at a collection that no longer exists. The failure reason typically asks you to set
`dataDeletionPolicy = RETAIN` on the data source and retry.

**This costs nothing and is safe to leave.** With no collection there are no OpenSearch Compute
Units, and the Knowledge Base itself is not billed. It will not disappear on its own either, so if
you want a clean account inventory, retry the delete in the documented order — data source first,
then the Knowledge Base:

```bash
aws bedrock-agent list-knowledge-bases --query "knowledgeBaseSummaries[?status=='DELETE_UNSUCCESSFUL'].[knowledgeBaseId,name]" --output text
aws bedrock-agent list-data-sources --knowledge-base-id <kb-id>
aws bedrock-agent update-data-source --knowledge-base-id <kb-id> --data-source-id <ds-id> \
  --data-deletion-policy RETAIN --name <ds-name> --data-source-configuration file://ds-config.json
aws bedrock-agent delete-data-source --knowledge-base-id <kb-id> --data-source-id <ds-id>
aws bedrock-agent delete-knowledge-base --knowledge-base-id <kb-id>
```

`scripts/cleanup.py` avoids it: it sets `dataDeletionPolicy` to RETAIN on each data source, deletes
the Knowledge Base, waits until it is gone, and only then deletes the collection. Re-running
`python scripts/cleanup.py --phase post-clean` also retries a Knowledge Base already stuck in this state.

### SageMaker AI MLflow App, Processing Jobs and Bedrock Evaluation Jobs

If `scripts/cleanup.py` could not remove them (these commands need an AWS CLI recent enough to
include the MLflow App and Bedrock evaluation commands):

```bash
# MLflow App created by scripts/setup_mlflow.py (exact name rai-healthcare-demo-mlflow)
aws sagemaker list-mlflow-apps --query "Summaries[?Name=='rai-healthcare-demo-mlflow'].Arn"
aws sagemaker delete-mlflow-app --arn <mlflow-app-arn>

# Stop a bias Processing job that is still running (processing jobs cannot be deleted)
aws sagemaker list-processing-jobs --name-contains rai-healthcare-demo-bias- --status-equals InProgress
aws sagemaker stop-processing-job --processing-job-name <job-name>

# Bedrock evaluation jobs created by scripts/04_evaluate_llm.py
aws bedrock list-evaluation-jobs --name-contains rai-healthcare-demo-eval
aws bedrock batch-delete-evaluation-job --job-identifiers <job-arn> [<job-arn> ...]
```

This sample creates no SageMaker endpoints, models or training jobs. `scripts/cleanup.py` still
deletes any whose name contains `rai-healthcare-demo`, for deployments made with an earlier version.

### Bedrock Guardrails

Remove the Automated Reasoning guardrail:

```bash
# List guardrails
aws bedrock list-guardrails

# Delete the guardrail
aws bedrock delete-guardrail --guardrail-identifier <guardrail-id>
```

### Bedrock Knowledge Base

If a knowledge base was created:

```bash
# List knowledge bases
aws bedrock-agent list-knowledge-bases

# Delete data sources first
aws bedrock-agent list-data-sources --knowledge-base-id <kb-id>
aws bedrock-agent delete-data-source --knowledge-base-id <kb-id> --data-source-id <ds-id>

# Then delete the knowledge base
aws bedrock-agent delete-knowledge-base --knowledge-base-id <kb-id>
```

### S3 Bucket (if not empty)

If Terraform cannot delete the S3 bucket due to remaining objects:

```bash
# Empty the bucket
aws s3 rm s3://rai-healthcare-demo-$AWS_ACCOUNT_ID-$AWS_REGION --recursive

# Delete the bucket
aws s3 rb s3://rai-healthcare-demo-$AWS_ACCOUNT_ID-$AWS_REGION
```

### CloudWatch Log Groups

```bash
aws logs delete-log-group --log-group-name /aws/agentcore/rai-healthcare-demo/diagnostic-agent
aws logs delete-log-group --log-group-name /aws/agentcore/rai-healthcare-demo/policy-enforcement
aws logs delete-log-group --log-group-name /aws/agentcore/rai-healthcare-demo/audit-trail
aws logs delete-log-group --log-group-name /aws/vpc/rai-healthcare-demo/flow-logs
```

## Cost Warning

The following resources incur charges even when idle:

| Resource | Idle Cost | Notes |
|----------|-----------|-------|
| AWS Network Firewall | ~$0.395/hr (~$288/month) | Charges apply as long as the firewall exists |
| VPC Endpoints (Interface) | ~$0.01/hr each | Three interface endpoints in this demo |
| SageMaker AI MLflow App | Not published | No MLflow App price appears on [SageMaker AI pricing](https://aws.amazon.com/sagemaker/ai/pricing/) or in the AWS Price List API; delete the app when you are done |

**Recommendation**: Always run `terraform destroy` immediately after completing the demo to avoid unexpected charges.

## Verification

The quickest check is the script's read-only phase, which reports any remaining IAM roles, S3
buckets, alarms, log groups, VPCs, SageMaker endpoints, the MLflow App, running bias Processing jobs and
Bedrock evaluation jobs for this project:

```bash
python scripts/cleanup.py --phase verify
```

To check by hand:

```bash
# Check for remaining IAM roles
aws iam list-roles --query "Roles[?contains(RoleName, 'rai-healthcare-demo')]" --output table

# Check for remaining S3 buckets
aws s3 ls | grep rai-healthcare-demo

# Check for remaining CloudWatch alarms
aws cloudwatch describe-alarms --alarm-name-prefix rai-healthcare-demo --query "MetricAlarms[].AlarmName"

# Check for remaining log groups
aws logs describe-log-groups --log-group-name-prefix /aws/agentcore/rai-healthcare-demo --query "logGroups[].logGroupName"
aws logs describe-log-groups --log-group-name-prefix /aws/sagemaker/rai-healthcare-demo --query "logGroups[].logGroupName"

# Check for remaining VPC
aws ec2 describe-vpcs --filters "Name=tag:Project,Values=rai-healthcare-demo" --query "Vpcs[].VpcId"

# Check for remaining SageMaker AI and Bedrock Evaluations resources
aws sagemaker list-endpoints --name-contains rai-healthcare-demo
aws sagemaker list-mlflow-apps --query "Summaries[?Name=='rai-healthcare-demo-mlflow']"
aws sagemaker list-processing-jobs --name-contains rai-healthcare-demo-bias- --status-equals InProgress
aws bedrock list-evaluation-jobs --name-contains rai-healthcare-demo-eval
```

If any of the above commands return results, manually delete those resources using the AWS CLI or Console.
