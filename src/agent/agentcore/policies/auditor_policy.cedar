// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
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
