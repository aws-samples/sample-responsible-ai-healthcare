# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
API Cleanup — Remove Demo Resources That Terraform Does Not Own
=================================================================
This script is the boto3 half of the teardown. It never shells out to another
program: the CLI steps (``terraform destroy``, ``cdk destroy``) live in the
Makefile, where invoking a CLI is the native idiom. ``make full-cleanup`` runs
the two halves in the right order; see docs/cleanup.md.

Phases (each selectable with --phase):
  pre-clean   Remove the Terraform blockers: orphaned SageMaker endpoints/
              configs/models, running bias and evaluation jobs, the MLflow
              App (scripts/setup_mlflow.py), and all object versions in the
              demo buckets.
  unblock     Remove the resources that make `terraform destroy` hang: EFS
              mount targets/filesystems and non-default security groups in the
              project VPC. Run this only after a destroy has failed.
  post-clean  Remove what Terraform does not manage: Bedrock guardrails, the
              Knowledge Base and its data sources, its OpenSearch Serverless
              collection and policies, Bedrock evaluation jobs, and demo log
              groups.
  verify      Report any project resources still present. Read-only.

Usage:
  make full-cleanup                            # the whole teardown, in order
  python scripts/cleanup.py                    # pre-clean + post-clean + verify
  python scripts/cleanup.py --force            # same, no confirmation prompt
  python scripts/cleanup.py --phase verify     # report only, deletes nothing
  python scripts/cleanup.py --phase unblock --force   # after a failed destroy

Exit codes: 0 on success, 2 when the user declines the confirmation prompt (so
`make full-cleanup` stops instead of going on to destroy infrastructure).
"""

import argparse
import logging
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import AWS_ACCOUNT_ID, BOTO_CONFIG, REGION, S3_BUCKET, validate_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_NAME = "rai-healthcare-demo"

# Teardown must never select a resource it does not own. This account can be shared,
# so every match below is an EXACT name, a project tag, or a project-scoped filter —
# never a substring like "rai", which also matches "guardrail" and "training".
SAGEMAKER_DOMAIN_NAME = f"{PROJECT_NAME}-studio"

# Guardrails this sample creates: the content filter used by the Lambda and the
# Automated Reasoning guardrail used by the agent runtime (scripts/setup_guardrail.py).
PROJECT_GUARDRAIL_NAMES = (
    f"{PROJECT_NAME}-guardrail",
    "rai-healthcare-diagnostic-guardrail",
)

# Knowledge Bases this sample creates (see docs/deployment.md step 4).
PROJECT_KNOWLEDGE_BASE_NAMES = (
    "rai-healthcare-clinical-guidelines",
    "rai-healthcare-clinical-guidelines-v2",
)

# OpenSearch Serverless vector store of the Knowledge Base (docs/deployment.md step 4).
# It bills for OpenSearch Compute Units while it exists, so teardown must delete it.
AOSS_COLLECTION_NAME = f"{PROJECT_NAME}-kb"
AOSS_SECURITY_POLICIES = (("encryption", f"{PROJECT_NAME}-enc"), ("network", f"{PROJECT_NAME}-net"))
AOSS_ACCESS_POLICY_NAME = f"{PROJECT_NAME}-access"
KB_DELETE_WAIT_SECONDS = 600

# Created by scripts/setup_mlflow.py, 02_bias_detection.py and 04_evaluate_llm.py.
# SageMaker AI processing jobs cannot be deleted (only stopped); they age out.
MLFLOW_APP_NAME = f"{PROJECT_NAME}-mlflow"
BIAS_JOB_PREFIX = f"{PROJECT_NAME}-bias-"
EVAL_JOB_PREFIX = f"{PROJECT_NAME}-eval-"
# ListEvaluationJobs nameContains cannot end in "-"; _project_jobs() applies the exact prefix
EVAL_NAME_FILTER = EVAL_JOB_PREFIX.rstrip("-")
BATCH_DELETE_LIMIT = 25

# Studio domains were removed from this sample; the EFS logic below still cleans up
# a deployment created before that change.
# Tag AWS adds to the EFS filesystem it creates for a SageMaker Studio domain.
SAGEMAKER_EFS_TAG = "ManagedByAmazonSageMakerResource"

# The egress firewall (infra/vpc.tf) sets delete_protection = true, so
# `terraform destroy` cannot delete it until this is turned off.
NETWORK_FIREWALL_NAME = f"{PROJECT_NAME}-egress"


def _is_project_firewall(name):
    """True only for this sample's firewall, matched by exact name."""
    return name == NETWORK_FIREWALL_NAME


def _project_role_names(roles):
    """Names of IAM roles that belong to this sample (name contains the project name)."""
    return sorted(r["RoleName"] for r in roles if PROJECT_NAME in r["RoleName"])


def _mount_targets_in_vpc(mount_targets, vpc_id):
    """Return only the mount targets that live in ``vpc_id``.

    Matching on VpcId alone is deliberate. An earlier version read
    ``mt.get("VpcId") == vpc_id or mt.get("SubnetId", "")``, which Python parses as
    ``(VpcId == vpc_id) or SubnetId`` — and because every real mount target has a
    non-empty SubnetId, that expression was always true and selected every mount
    target in the Region, including other projects'.
    """
    return [mt for mt in mount_targets if mt.get("VpcId") == vpc_id]


def _project_domain_ids(sm_client):
    """Return the SageMaker domain IDs belonging to this project, by exact name."""
    domains = sm_client.list_domains().get("Domains", [])
    return {d["DomainId"] for d in domains if d.get("DomainName") == SAGEMAKER_DOMAIN_NAME}


def _is_project_filesystem(filesystem, domain_ids):
    """True only if this EFS filesystem belongs to the project.

    A filesystem qualifies when its name carries the project name, or when it is the
    one SageMaker created for a project Studio domain (identified by the
    ``ManagedByAmazonSageMakerResource`` tag). A missing name is NOT ownership: other
    teams' unnamed filesystems used to match and be deleted.
    """
    name = filesystem.get("Name") or ""
    if PROJECT_NAME in name:
        return True

    for tag in filesystem.get("Tags") or []:
        if tag.get("Key") != SAGEMAKER_EFS_TAG:
            continue
        value = tag.get("Value") or ""
        if any(domain_id in value for domain_id in domain_ids):
            return True

    return False


def _project_mlflow_apps(apps):
    """ARNs of the project's MLflow Apps, by exact name, not already being deleted."""
    return [
        a["Arn"] for a in apps if a.get("Name") == MLFLOW_APP_NAME and a.get("Status") not in ("Deleting", "Deleted")
    ]


def _project_jobs(names, prefix):
    """Job names this sample created: the exact project prefix, never a substring match."""
    return [n for n in names if n.startswith(prefix)]


def _is_project_guardrail(name):
    """True only for the guardrails this sample creates, matched exactly.

    A substring test such as ``"rai" in name.lower()`` matches any name containing
    "guardrail" or "training" — for example an unrelated ``promptarmor-guardrail``.
    """
    return name in PROJECT_GUARDRAIL_NAMES


def _is_project_knowledge_base(name):
    """True only for the Knowledge Bases this sample creates, matched exactly."""
    return name in PROJECT_KNOWLEDGE_BASE_NAMES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def confirm(msg, force=False):
    """Prompt user for confirmation unless --force is set."""
    if force:
        return True
    response = input(f"\n  {msg} [y/N]: ").strip().lower()
    return response in ("y", "yes")


def safe_call(func, description):
    """Run a cleanup function, catch and log errors without aborting."""
    try:
        func()
    except Exception as e:
        logger.warning(f"{description} failed: {e}")


# ---------------------------------------------------------------------------
# Pre-clean: Remove Terraform blockers
# ---------------------------------------------------------------------------


def cleanup_sagemaker_endpoints():
    """Delete orphaned SageMaker endpoints/configs/models matching project pattern."""
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)

    endpoints = sm.list_endpoints(NameContains=PROJECT_NAME)["Endpoints"]
    for ep in endpoints:
        name = ep["EndpointName"]
        logger.info(f"Deleting SageMaker endpoint: {name}")
        sm.delete_endpoint(EndpointName=name)

    configs = sm.list_endpoint_configs(NameContains=PROJECT_NAME)["EndpointConfigs"]
    for cfg in configs:
        name = cfg["EndpointConfigName"]
        logger.info(f"Deleting endpoint config: {name}")
        sm.delete_endpoint_config(EndpointConfigName=name)

    models = sm.list_models(NameContains=PROJECT_NAME)["Models"]
    for model in models:
        name = model["ModelName"]
        logger.info(f"Deleting model: {name}")
        sm.delete_model(ModelName=name)

    if not endpoints and not configs and not models:
        logger.info("No orphaned SageMaker resources found")


def _list_mlflow_apps(sm):
    apps, token = [], None
    while True:
        page = sm.list_mlflow_apps(**({"NextToken": token} if token else {}))
        apps += page.get("Summaries", [])
        token = page.get("NextToken")
        if not token:
            return apps


def _list_evaluation_jobs(bedrock, **filters):
    jobs, token = [], None
    while True:
        page = bedrock.list_evaluation_jobs(
            nameContains=EVAL_NAME_FILTER, **filters, **({"nextToken": token} if token else {})
        )
        jobs += page.get("jobSummaries", [])
        token = page.get("nextToken")
        if not token:
            return jobs


def _running_processing_jobs(sm):
    names = []
    for page in sm.get_paginator("list_processing_jobs").paginate(
        NameContains=BIAS_JOB_PREFIX, StatusEquals="InProgress"
    ):
        names += [j["ProcessingJobName"] for j in page["ProcessingJobSummaries"]]
    return _project_jobs(names, BIAS_JOB_PREFIX)


def stop_running_jobs():
    """Stop in-progress bias processing jobs and Bedrock evaluation jobs."""
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)
    for name in _running_processing_jobs(sm):
        logger.info(f"Stopping processing job: {name}")
        sm.stop_processing_job(ProcessingJobName=name)
    bedrock = boto3.client("bedrock", region_name=REGION, config=BOTO_CONFIG)
    for job in _list_evaluation_jobs(bedrock, statusEquals="InProgress"):
        if _project_jobs([job["jobName"]], EVAL_JOB_PREFIX):
            logger.info(f"Stopping evaluation job: {job['jobName']}")
            bedrock.stop_evaluation_job(jobIdentifier=job["jobArn"])


def delete_mlflow_app(wait_seconds=600):
    """Delete the MLflow App before Terraform removes its role, bucket and key."""
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)
    arns = _project_mlflow_apps(_list_mlflow_apps(sm))
    for arn in arns:
        logger.info(f"Deleting MLflow App: {arn}")
        sm.delete_mlflow_app(Arn=arn)
    deadline = time.time() + wait_seconds
    while arns and time.time() < deadline:
        live = {a["Arn"] for a in _list_mlflow_apps(sm) if a.get("Status") != "Deleted"}
        arns = [a for a in arns if a in live]
        if arns:
            time.sleep(15)
    if arns:
        logger.warning(f"MLflow App still deleting after {wait_seconds}s: {arns}")
    else:
        logger.info("No MLflow App left")


def delete_evaluation_jobs():
    """Delete the finished Bedrock evaluation jobs this sample created."""
    bedrock = boto3.client("bedrock", region_name=REGION, config=BOTO_CONFIG)
    jobs = [
        j["jobArn"]
        for j in _list_evaluation_jobs(bedrock)
        if j["status"] in ("Completed", "Failed", "Stopped") and _project_jobs([j["jobName"]], EVAL_JOB_PREFIX)
    ]
    for i in range(0, len(jobs), BATCH_DELETE_LIMIT):
        result = bedrock.batch_delete_evaluation_job(jobIdentifiers=jobs[i : i + BATCH_DELETE_LIMIT])
        for error in result.get("errors", []):
            logger.warning(f"Could not delete evaluation job {error.get('jobIdentifier')}: {error.get('message')}")
    logger.info(f"Deleted {len(jobs)} evaluation job(s)" if jobs else "No evaluation jobs found")


def disable_firewall_delete_protection():
    """Turn off delete protection on the project firewall so `terraform destroy` can remove it."""
    nfw = boto3.client("network-firewall", region_name=REGION, config=BOTO_CONFIG)
    try:
        response = nfw.describe_firewall(FirewallName=NETWORK_FIREWALL_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.info(f"Network Firewall {NETWORK_FIREWALL_NAME} not found (already deleted)")
            return
        raise
    firewall = response["Firewall"]
    if not _is_project_firewall(firewall["FirewallName"]):
        logger.warning(f"Refusing to change firewall {firewall['FirewallName']}: not this project's")
        return
    if not firewall.get("DeleteProtection"):
        logger.info(f"Network Firewall {NETWORK_FIREWALL_NAME} delete protection already off")
        return
    logger.info(f"Turning off delete protection on Network Firewall {NETWORK_FIREWALL_NAME}")
    nfw.update_firewall_delete_protection(
        FirewallName=NETWORK_FIREWALL_NAME, DeleteProtection=False, UpdateToken=response["UpdateToken"]
    )


def empty_s3_bucket(bucket_name):
    """Delete all object versions and delete markers from a versioned bucket."""
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)

    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError:
        logger.info(f"Bucket {bucket_name} does not exist, skipping")
        return

    logger.info(f"Emptying bucket: {bucket_name} (all versions)...")
    total_deleted = 0

    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket_name):
        objects_to_delete = []

        for version in page.get("Versions", []):
            objects_to_delete.append({"Key": version["Key"], "VersionId": version["VersionId"]})

        for marker in page.get("DeleteMarkers", []):
            objects_to_delete.append({"Key": marker["Key"], "VersionId": marker["VersionId"]})

        if objects_to_delete:
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i : i + 1000]
                s3.delete_objects(Bucket=bucket_name, Delete={"Objects": batch, "Quiet": True})
                total_deleted += len(batch)

    logger.info(f"Deleted {total_deleted} object versions from {bucket_name}")


# ---------------------------------------------------------------------------
# Unblock: EFS and Security Group cleanup
# ---------------------------------------------------------------------------


def find_vpc_id():
    """Find the project VPC by tag."""
    ec2 = boto3.client("ec2", region_name=REGION, config=BOTO_CONFIG)
    response = ec2.describe_vpcs(Filters=[{"Name": "tag:Project", "Values": [PROJECT_NAME]}])
    vpcs = response.get("Vpcs", [])
    if vpcs:
        return vpcs[0]["VpcId"]
    return None


def cleanup_efs_blockers(vpc_id):
    """Delete the project's EFS mount targets and filesystems blocking subnet deletion."""
    efs = boto3.client("efs", region_name=REGION, config=BOTO_CONFIG)
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)

    domain_ids = _project_domain_ids(sm)

    filesystems = efs.describe_file_systems().get("FileSystems", [])
    for fs in filesystems:
        fs_id = fs["FileSystemId"]

        if not _is_project_filesystem(fs, domain_ids):
            logger.debug("Skipping EFS filesystem %s — not owned by %s", fs_id, PROJECT_NAME)
            continue

        all_mount_targets = efs.describe_mount_targets(FileSystemId=fs_id).get("MountTargets", [])
        mount_targets = _mount_targets_in_vpc(all_mount_targets, vpc_id)

        for mt in mount_targets:
            logger.info(f"Deleting EFS mount target: {mt['MountTargetId']} (fs: {fs_id})")
            efs.delete_mount_target(MountTargetId=mt["MountTargetId"])

        # Wait for mount targets to be deleted before removing filesystem
        if mount_targets:
            logger.info("Waiting 30s for mount target ENIs to release...")
            time.sleep(30)

        try:
            efs.delete_file_system(FileSystemId=fs_id)
            logger.info(f"Deleted EFS filesystem: {fs_id}")
        except ClientError as e:
            logger.warning(f"Could not delete EFS {fs_id}: {e}")


def cleanup_security_groups(vpc_id):
    """Delete non-default security groups in the project VPC."""
    ec2 = boto3.client("ec2", region_name=REGION, config=BOTO_CONFIG)

    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"]

    for sg in sgs:
        if sg["GroupName"] == "default":
            continue
        sg_id = sg["GroupId"]
        logger.info(f"Deleting security group: {sg_id} ({sg['GroupName']})")
        try:
            ec2.delete_security_group(GroupId=sg_id)
        except ClientError as e:
            logger.warning(f"Could not delete SG {sg_id}: {e}")


# ---------------------------------------------------------------------------
# Post-Terraform: Resources outside Terraform management
# ---------------------------------------------------------------------------


def cleanup_bedrock_guardrails():
    """Delete Bedrock guardrails matching project name."""
    bedrock = boto3.client("bedrock", region_name=REGION, config=BOTO_CONFIG)

    try:
        response = bedrock.list_guardrails()
        for g in response.get("guardrails", []):
            if _is_project_guardrail(g["name"]):
                logger.info(f"Deleting guardrail: {g['name']} (ID: {g['id']})")
                bedrock.delete_guardrail(guardrailIdentifier=g["id"])
    except ClientError as e:
        logger.warning(f"Guardrail cleanup: {e}")


def cleanup_knowledge_base():
    """Delete the project Knowledge Bases and their data sources.

    Returns the OpenSearch Serverless collection ARNs they used, so the vector store
    can be deleted afterwards even when the console generated its name. Each data
    source is first switched to dataDeletionPolicy RETAIN: the whole collection is
    deleted next, and with the default DELETE a missing or unreachable vector store
    leaves the Knowledge Base stuck in DELETE_UNSUCCESSFUL.
    """
    bedrock_agent = boto3.client("bedrock-agent", region_name=REGION, config=BOTO_CONFIG)
    collection_arns = []

    try:
        response = bedrock_agent.list_knowledge_bases()
        for kb in response.get("knowledgeBaseSummaries", []):
            if _is_project_knowledge_base(kb["name"]):
                kb_id = kb["knowledgeBaseId"]
                storage = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
                arn = storage.get("storageConfiguration", {}).get("opensearchServerlessConfiguration", {})
                if arn.get("collectionArn"):
                    collection_arns.append(arn["collectionArn"])
                logger.info(f"Deleting Knowledge Base: {kb['name']} (ID: {kb_id})")

                ds_response = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
                for ds in ds_response.get("dataSourceSummaries", []):
                    _retain_vector_data(bedrock_agent, kb_id, ds["dataSourceId"])
                    bedrock_agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"])
                    logger.info(f"  Deleted data source: {ds['dataSourceId']}")

                bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
                _wait_for_knowledge_base_deletion(bedrock_agent, kb_id)
    except ClientError as e:
        logger.warning(f"Knowledge Base cleanup: {e}")
    return collection_arns


def _retain_vector_data(bedrock_agent, kb_id, ds_id):
    ds = bedrock_agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)["dataSource"]
    if ds.get("dataDeletionPolicy") == "RETAIN":
        return
    optional = ("description", "serverSideEncryptionConfiguration", "vectorIngestionConfiguration")
    bedrock_agent.update_data_source(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
        name=ds["name"],
        dataSourceConfiguration=ds["dataSourceConfiguration"],
        dataDeletionPolicy="RETAIN",
        **{k: ds[k] for k in optional if k in ds},
    )


def _wait_for_knowledge_base_deletion(bedrock_agent, kb_id, wait_seconds=KB_DELETE_WAIT_SECONDS):
    """Wait until the Knowledge Base is gone, so its vector store is deleted only afterwards.

    Deleting the collection while the Knowledge Base is still DELETING leaves the
    Knowledge Base in DELETE_UNSUCCESSFUL, because it can no longer remove its data.
    """
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            status = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return
            raise
        if status == "DELETE_UNSUCCESSFUL":
            raise RuntimeError(f"Knowledge Base {kb_id} is DELETE_UNSUCCESSFUL; see docs/cleanup.md")
        time.sleep(10)
    raise RuntimeError(f"Knowledge Base {kb_id} still exists after {wait_seconds}s")


def cleanup_opensearch_serverless(collection_arns=(), wait_seconds=KB_DELETE_WAIT_SECONDS):
    """Delete the Knowledge Base vector store and its policies.

    The collection is the one named AOSS_COLLECTION_NAME plus any collection a project
    Knowledge Base used (collection_arns, from cleanup_knowledge_base). Skipped while a
    project Knowledge Base is still being deleted, so it can finish cleanly.
    """
    agent = boto3.client("bedrock-agent", region_name=REGION, config=BOTO_CONFIG)
    left = [
        kb["name"]
        for kb in agent.list_knowledge_bases().get("knowledgeBaseSummaries", [])
        if _is_project_knowledge_base(kb["name"]) and kb.get("status") != "DELETE_UNSUCCESSFUL"
    ]
    if left:
        logger.warning(f"Not deleting the OpenSearch Serverless collection: Knowledge Bases remain: {left}")
        return

    aoss = boto3.client("opensearchserverless", region_name=REGION, config=BOTO_CONFIG)
    ids = sorted({arn.rsplit("/", 1)[-1] for arn in collection_arns})

    def remaining():
        found = aoss.batch_get_collection(names=[AOSS_COLLECTION_NAME]).get("collectionDetails", [])
        if ids:
            found += aoss.batch_get_collection(ids=ids).get("collectionDetails", [])
        return {c["id"]: c for c in found}

    for collection in remaining().values():
        if collection.get("status") != "DELETING":
            logger.info(f"Deleting OpenSearch Serverless collection: {collection['name']} (ID: {collection['id']})")
            aoss.delete_collection(id=collection["id"])
    # The encryption and network policies cannot be deleted while a collection uses them.
    deadline = time.monotonic() + wait_seconds
    while remaining():
        if time.monotonic() > deadline:
            raise RuntimeError(f"Collection {AOSS_COLLECTION_NAME} still exists after {wait_seconds}s")
        time.sleep(10)

    for policy_type, name in AOSS_SECURITY_POLICIES:
        _delete_if_present(aoss.delete_security_policy, type=policy_type, name=name)
    _delete_if_present(aoss.delete_access_policy, type="data", name=AOSS_ACCESS_POLICY_NAME)


def _delete_if_present(delete, **kwargs):
    try:
        delete(**kwargs)
        logger.info(f"Deleted OpenSearch Serverless {kwargs['type']} policy {kwargs['name']}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise


def cleanup_cloudwatch_logs():
    """Delete CloudWatch log groups created by the demo."""
    logs = boto3.client("logs", region_name=REGION, config=BOTO_CONFIG)

    prefixes = [
        f"/aws/agentcore/{PROJECT_NAME}/",
        f"/aws/sagemaker/{PROJECT_NAME}/",
        f"/aws/vpc/{PROJECT_NAME}/",
    ]

    for prefix in prefixes:
        try:
            response = logs.describe_log_groups(logGroupNamePrefix=prefix)
            for lg in response.get("logGroups", []):
                name = lg["logGroupName"]
                logger.info(f"Deleting log group: {name}")
                logs.delete_log_group(logGroupName=name)
        except ClientError as e:
            logger.warning(f"Log group cleanup ({prefix}): {e}")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_cleanup():
    """Check for remaining project resources and report."""
    print()
    print("=" * 60)
    print("  VERIFICATION — Checking for remaining resources")
    print("=" * 60)
    print()

    remaining = []
    unchecked = []

    # IAM roles (paginated: a shared account has more than one page of roles)
    iam = boto3.client("iam", config=BOTO_CONFIG)
    try:
        pages = iam.get_paginator("list_roles").paginate()
        project_roles = _project_role_names(role for page in pages for role in page["Roles"])
        if project_roles:
            remaining.append(("IAM Roles", project_roles))
    except ClientError as e:
        unchecked.append(f"IAM roles: {e.response['Error']['Code']}")

    # S3 buckets
    s3 = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    try:
        buckets = s3.list_buckets()["Buckets"]
        project_buckets = [b["Name"] for b in buckets if PROJECT_NAME in b["Name"]]
        if project_buckets:
            remaining.append(("S3 Buckets", project_buckets))
    except ClientError as e:
        unchecked.append(f"S3 buckets: {e.response['Error']['Code']}")

    # CloudWatch alarms
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    try:
        alarms = cw.describe_alarms(AlarmNamePrefix=PROJECT_NAME)["MetricAlarms"]
        if alarms:
            remaining.append(("CloudWatch Alarms", [a["AlarmName"] for a in alarms]))
    except ClientError as e:
        unchecked.append(f"CloudWatch alarms: {e.response['Error']['Code']}")

    # Log groups
    logs = boto3.client("logs", region_name=REGION, config=BOTO_CONFIG)
    log_groups_found = []
    for prefix in [f"/aws/agentcore/{PROJECT_NAME}", f"/aws/sagemaker/{PROJECT_NAME}"]:
        try:
            response = logs.describe_log_groups(logGroupNamePrefix=prefix)
            log_groups_found.extend(lg["logGroupName"] for lg in response.get("logGroups", []))
        except ClientError as e:
            unchecked.append(f"log groups {prefix}: {e.response['Error']['Code']}")
    if log_groups_found:
        remaining.append(("CloudWatch Log Groups", log_groups_found))

    # VPC
    ec2 = boto3.client("ec2", region_name=REGION, config=BOTO_CONFIG)
    try:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Project", "Values": [PROJECT_NAME]}])["Vpcs"]
        if vpcs:
            remaining.append(("VPCs", [v["VpcId"] for v in vpcs]))
    except ClientError as e:
        unchecked.append(f"VPCs: {e.response['Error']['Code']}")

    # SageMaker endpoints
    sm = boto3.client("sagemaker", region_name=REGION, config=BOTO_CONFIG)
    try:
        eps = sm.list_endpoints(NameContains=PROJECT_NAME)["Endpoints"]
        if eps:
            remaining.append(("SageMaker Endpoints", [e["EndpointName"] for e in eps]))
    except ClientError as e:
        unchecked.append(f"SageMaker endpoints: {e.response['Error']['Code']}")
    try:
        apps = _project_mlflow_apps(_list_mlflow_apps(sm))
        if apps:
            remaining.append(("SageMaker AI MLflow App", apps))
        running = _running_processing_jobs(sm)
        if running:
            remaining.append(("Running processing jobs", running))
    except ClientError as e:
        unchecked.append(f"SageMaker AI MLflow App / processing jobs: {e.response['Error']['Code']}")

    # Bedrock evaluation jobs
    try:
        bedrock = boto3.client("bedrock", region_name=REGION, config=BOTO_CONFIG)
        jobs = _project_jobs([j["jobName"] for j in _list_evaluation_jobs(bedrock)], EVAL_JOB_PREFIX)
        if jobs:
            remaining.append(("Bedrock evaluation jobs", jobs))
    except ClientError as e:
        unchecked.append(f"Bedrock evaluation jobs: {e.response['Error']['Code']}")

    # Knowledge Bases and the OpenSearch Serverless collection (exact names)
    try:
        agent = boto3.client("bedrock-agent", region_name=REGION, config=BOTO_CONFIG)
        kbs = [
            f"{kb['name']} ({kb['status']})"
            for kb in agent.list_knowledge_bases().get("knowledgeBaseSummaries", [])
            if _is_project_knowledge_base(kb["name"])
        ]
        if kbs:
            remaining.append(("Bedrock Knowledge Bases", kbs))
    except ClientError as e:
        unchecked.append(f"Knowledge Bases: {e.response['Error']['Code']}")
    try:
        aoss = boto3.client("opensearchserverless", region_name=REGION, config=BOTO_CONFIG)
        found = aoss.batch_get_collection(names=[AOSS_COLLECTION_NAME]).get("collectionDetails", [])
        if found:
            remaining.append(("OpenSearch Serverless collection", [f"{c['name']} ({c['status']})" for c in found]))
    except ClientError as e:
        unchecked.append(f"OpenSearch Serverless: {e.response['Error']['Code']}")

    # Network Firewall, IAM Access Analyzer and the Lambda function (exact names)
    exact_checks = (
        ("Network Firewall", "network-firewall", lambda c: c.describe_firewall(FirewallName=NETWORK_FIREWALL_NAME)),
        ("Lambda Function", "lambda", lambda c: c.get_function(FunctionName=f"{PROJECT_NAME}-preprocessing")),
        ("IAM Access Analyzer", "accessanalyzer", lambda c: c.get_analyzer(analyzerName=f"{PROJECT_NAME}-account")),
    )
    for label, service, probe in exact_checks:
        try:
            probe(boto3.client(service, region_name=REGION, config=BOTO_CONFIG))
            remaining.append((label, [label.lower()]))
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("ResourceNotFoundException", "NotFoundException"):
                unchecked.append(f"{label}: {e.response['Error']['Code']}")

    # Report
    if remaining:
        print("  ⚠️  REMAINING RESOURCES FOUND:")
        print()
        for category, items in remaining:
            print(f"  {category}:")
            for item in items:
                print(f"    - {item}")
            print()
        print("  Run this script again or delete manually via AWS Console.")
    if unchecked:
        # A check that could not run is not evidence of a clean account
        print("  ⚠️  COULD NOT CHECK:")
        for item in unchecked:
            print(f"    - {item}")
    if not remaining and not unchecked:
        print("  ✅ ALL CLEAN — No project resources found")

    print()
    print("=" * 60)
    return not remaining and not unchecked


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_pre_clean():
    """Remove the resources that block `terraform destroy`."""
    print()
    logger.info("Phase pre-clean: removing Terraform blockers...")

    safe_call(cleanup_sagemaker_endpoints, "SageMaker endpoint cleanup")
    safe_call(stop_running_jobs, "Stopping bias and evaluation jobs")
    safe_call(delete_mlflow_app, "MLflow App deletion")
    safe_call(disable_firewall_delete_protection, "Network Firewall delete protection")

    # Empty S3 buckets (versioned objects block terraform destroy)
    if S3_BUCKET:
        safe_call(lambda: empty_s3_bucket(S3_BUCKET), "S3 bucket emptying")

    # Also empty the access logs bucket and the cross-Region replicas (Terraform uses
    # bucket_prefix, so names have a random suffix; see infra/replication.tf)
    s3_client = boto3.client("s3", region_name=REGION, config=BOTO_CONFIG)
    prefixes = (
        f"{PROJECT_NAME}-logs-",
        f"{PROJECT_NAME[:20]}-data-rep-",
        f"{PROJECT_NAME[:20]}-logs-rep-",
        # Kept for deployments created before server access logs moved to
        # CloudWatch Logs; current deployments have no *-s3al-* bucket.
        f"{PROJECT_NAME[:20]}-s3al-",
    )
    try:
        all_buckets = s3_client.list_buckets().get("Buckets", [])
        logs_buckets = [b["Name"] for b in all_buckets if b["Name"].startswith(prefixes)]
        for logs_bucket in logs_buckets:
            safe_call(lambda b=logs_bucket: empty_s3_bucket(b), f"S3 logs bucket emptying ({logs_bucket})")
    except ClientError as e:
        logger.warning(f"Could not list buckets for logs cleanup: {e}")


def run_unblock():
    """Remove the EFS mount targets and security groups that stall a destroy."""
    print()
    logger.info("Phase unblock: clearing VPC/EFS blockers left by a failed destroy...")

    vpc_id = find_vpc_id()
    if vpc_id:
        logger.info(f"Found VPC: {vpc_id}")
        safe_call(lambda: cleanup_efs_blockers(vpc_id), "EFS cleanup")
        safe_call(lambda: cleanup_security_groups(vpc_id), "Security group cleanup")
    else:
        logger.info("No project VPC found (may already be deleted)")


def run_post_clean():
    """Remove the resources Terraform does not manage."""
    print()
    logger.info("Phase post-clean: removing resources outside Terraform...")

    safe_call(cleanup_bedrock_guardrails, "Bedrock guardrail cleanup")
    collection_arns = []
    safe_call(lambda: collection_arns.extend(cleanup_knowledge_base()), "Knowledge Base cleanup")
    safe_call(lambda: cleanup_opensearch_serverless(collection_arns), "OpenSearch Serverless cleanup")
    safe_call(delete_evaluation_jobs, "Bedrock evaluation job cleanup")
    safe_call(cleanup_cloudwatch_logs, "CloudWatch log group cleanup")


def run_verify():
    """Report what is left. Deletes nothing."""
    all_clean = verify_cleanup()

    print()
    if all_clean:
        print("  🎉 ALL CLEAN — No project resources found")
    else:
        print("  ⚠️  SOME RESOURCES REMAIN OR COULD NOT BE CHECKED (see above)")
        raise SystemExit(1)


PHASES = {
    "pre-clean": run_pre_clean,
    "unblock": run_unblock,
    "post-clean": run_post_clean,
    "verify": run_verify,
}

# What `python scripts/cleanup.py` does with no --phase: everything that is safe
# to run standalone. "unblock" is excluded because it deletes EFS filesystems and
# security groups, and is only correct after a destroy has already failed.
DEFAULT_PHASES = ("pre-clean", "post-clean", "verify")


def main():
    parser = argparse.ArgumentParser(
        description="Remove RAI Healthcare Demo resources that Terraform does not own. "
        "The CLI steps (terraform destroy, cdk destroy) live in the Makefile — run `make full-cleanup` "
        "for the complete teardown."
    )
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompts (CI mode)")
    parser.add_argument(
        "--phase",
        choices=sorted(PHASES),
        action="append",
        metavar="PHASE",
        help=(
            "Phase to run; repeatable. One of: "
            + ", ".join(sorted(PHASES))
            + f". Default: {' + '.join(DEFAULT_PHASES)}"
        ),
    )
    args = parser.parse_args()

    phases = tuple(args.phase) if args.phase else DEFAULT_PHASES
    deletes = [p for p in phases if p != "verify"]

    # Teardown needs no guardrail: the guardrails are among the things it deletes, and a
    # fresh shell has no GUARDRAIL_ID.
    if not validate_config(require_guardrail_id=False):
        raise SystemExit(1)

    print("=" * 60)
    print("  RAI Healthcare Demo — API Cleanup")
    print("=" * 60)
    print()
    print(f"  Account:  {AWS_ACCOUNT_ID}")
    print(f"  Region:   {REGION}")
    print(f"  Bucket:   {S3_BUCKET}")
    print(f"  Project:  {PROJECT_NAME}")
    print(f"  Phases:   {', '.join(phases)}")
    print()
    if deletes:
        print("  This DELETES demo resources:")
        if "pre-clean" in phases:
            print("    - SageMaker endpoints/configs/models (if orphaned)")
            print(f"    - Running {BIAS_JOB_PREFIX}* and {EVAL_JOB_PREFIX}* jobs (stopped)")
            print(f"    - The MLflow App {MLFLOW_APP_NAME}")
            print("    - Every object version in the demo S3 buckets")
            print(f"    - Delete protection on the Network Firewall {NETWORK_FIREWALL_NAME} (turned off)")
        if "unblock" in phases:
            print("    - EFS mount targets/filesystems and non-default security groups in the project VPC")
        if "post-clean" in phases:
            print("    - Bedrock guardrails, the Knowledge Base and its data sources")
            print(f"    - Their OpenSearch Serverless collection ({AOSS_COLLECTION_NAME}) and its policies")
            print(f"    - Finished Bedrock evaluation jobs named {EVAL_JOB_PREFIX}*")
            print("    - Demo CloudWatch log groups")
        print()
        print("  Terraform-managed infrastructure is not deleted here (only the firewall's delete")
        print("  protection is turned off) — use `make destroy`.")
        print()

        if not confirm("Proceed?", force=args.force):
            print("  Aborted.")
            raise SystemExit(2)

    for phase in phases:
        PHASES[phase]()

    print("=" * 60)


if __name__ == "__main__":
    main()
