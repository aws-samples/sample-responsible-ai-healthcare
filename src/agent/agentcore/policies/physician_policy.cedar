// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
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
