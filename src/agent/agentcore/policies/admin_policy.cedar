// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
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
