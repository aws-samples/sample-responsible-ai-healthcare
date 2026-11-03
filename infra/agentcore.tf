# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
###############################################################################
# AgentCore — Policy Engine & Cedar Policies
#
# Note: AgentCore resources are deployed via the AgentCore CLI/CDK as Terraform
# provider support is limited. This file stores the Cedar policy documents in
# S3 for version control and reference.
#
# IMPORTANT — The Cedar policies in this file and in `src/agent/agentcore.json`
# are ILLUSTRATIVE and are NOT enforced at runtime. This sample deploys no
# AgentCore Gateway (`agentCoreGateways` is empty), so nothing evaluates Cedar
# on invocation. The per-action policies (ViewRecommendation,
# OverrideRecommendation, ViewTriageScore, etc.) show the intended RBAC model
# and are stored in S3 as a reference pattern.
#
# For production: deploy an AgentCore Gateway in front of the runtime and attach
# a Policy Engine that evaluates these policies on every invocation.
#
# The access control that IS enforced is IAM: resource-level access to patient
# data, reports, and metrics (see iam.tf), tested live in
# scripts/06_test_access_control.py.
#
# Deployment command:
#   cd src/agent/ && agentcore deploy
###############################################################################

# ─────────────────────────────────────────────────────────────────────────────
# Cedar Policy Files (stored in S3 for version control)
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_object" "cedar_physician_policy" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/policies/physician_policy.cedar"
  content = <<-CEDAR
    // Physician Policy — Full access to AI diagnostic outputs for treating patients
    permit(
      principal in Role::"Physician",
      action in [
        Action::"ViewRecommendation",
        Action::"ViewConfidenceScore",
        Action::"ViewSHAPExplanation",
        Action::"ViewTreatmentPlan",
        Action::"ViewEvidenceCitations",
        Action::"OverrideRecommendation",
        Action::"ProvideFeedback",
        Action::"RequestSecondOpinion"
      ],
      resource
    ) when {
      resource.treatingPhysician == principal.employeeId
    };

    // Emergency override — any physician can access any patient in emergency
    permit(
      principal in Role::"Physician",
      action == Action::"EmergencyOverride",
      resource
    ) when {
      context.emergencyFlag == true
    };
  CEDAR
}

resource "aws_s3_object" "cedar_nurse_policy" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/policies/nurse_policy.cedar"
  content = <<-CEDAR
    // Nurse Policy — Limited access to triage scores only
    permit(
      principal in Role::"Nurse",
      action in [
        Action::"ViewTriageScore",
        Action::"ViewCriticalAlerts",
        Action::"ViewPriorityLevel",
        Action::"AcknowledgeAlert"
      ],
      resource
    ) when {
      resource.ward == principal.assignedWard
    };

    // Deny nurses from viewing full recommendations
    forbid(
      principal in Role::"Nurse",
      action in [
        Action::"ViewRecommendation",
        Action::"ViewSHAPExplanation",
        Action::"ViewTreatmentPlan",
        Action::"OverrideRecommendation"
      ],
      resource
    );
  CEDAR
}

resource "aws_s3_object" "cedar_admin_policy" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/policies/admin_policy.cedar"
  content = <<-CEDAR
    // Admin Policy — Governance and configuration, no patient PHI
    permit(
      principal in Role::"Admin",
      action in [
        Action::"ViewSystemMetrics",
        Action::"ViewAggregateAccuracy",
        Action::"ViewBiasReports",
        Action::"ConfigureThresholds",
        Action::"ManageModelVersions",
        Action::"ViewComplianceReports",
        Action::"ManageUserRoles",
        Action::"ViewAuditLogs"
      ],
      resource in ResourceType::"System"
    );

    // Deny admins from accessing patient data
    forbid(
      principal in Role::"Admin",
      action in [
        Action::"ViewRecommendation",
        Action::"ViewPatientHistory",
        Action::"ViewLabResults"
      ],
      resource in ResourceType::"Patient"
    );
  CEDAR
}

resource "aws_s3_object" "cedar_auditor_policy" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "${var.project_name}/policies/auditor_policy.cedar"
  content = <<-CEDAR
    // Auditor Policy — Read-only audit trails, de-identified data only
    permit(
      principal in Role::"Auditor",
      action in [
        Action::"ViewAuditTrails",
        Action::"ViewComplianceReports",
        Action::"ViewBiasReports",
        Action::"ViewModelCards",
        Action::"ViewPolicyHistory",
        Action::"ViewAggregateMetrics",
        Action::"ExportComplianceData"
      ],
      resource in ResourceType::"Audit"
    );

    // Deny auditors from any patient data or system modification
    forbid(
      principal in Role::"Auditor",
      action,
      resource in ResourceType::"Patient"
    );

    forbid(
      principal in Role::"Auditor",
      action in [
        Action::"ConfigureThresholds",
        Action::"ManageModelVersions",
        Action::"ManageUserRoles",
        Action::"OverrideRecommendation"
      ],
      resource
    );
  CEDAR
}
