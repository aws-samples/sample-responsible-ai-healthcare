// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
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
