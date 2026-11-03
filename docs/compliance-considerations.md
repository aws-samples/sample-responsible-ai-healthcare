# Compliance Considerations

This document outlines what additional controls and certifications would be required to move from this **demonstration architecture** to a production healthcare AI deployment.

> **This sample is NOT compliant with HIPAA, FDA, or any healthcare regulatory framework as-is.** It is designed purely for educational and demonstration purposes using synthetic data.

---

## What This Demo Does NOT Provide

| Requirement | Status in This Demo | Production Requirement |
|---|---|---|
| HIPAA compliance | Not configured | BAA with AWS, encryption controls, access logging, minimum necessary |
| FDA clearance/approval | Not applicable | 510(k), De Novo, or PMA pathway for clinical decision support |
| Clinical validation | Not performed | Prospective clinical studies, peer-reviewed evidence |
| Real patient data handling | Synthetic only | PHI encryption, access controls, audit trails, data retention policies |
| Business Associate Agreement | None | Required before processing PHI on AWS |
| HITRUST certification | None | Common for healthcare SaaS |
| SOC 2 Type II | None | Recommended for healthcare services |

---

## HIPAA Considerations

If adapting this architecture for real PHI:

1. **Business Associate Agreement (BAA)**: Execute a BAA with AWS before processing PHI
2. **HIPAA-Eligible Services Only**: Use only [AWS HIPAA-eligible services](https://aws.amazon.com/compliance/hipaa-eligible-services-reference/)
3. **Encryption**: Enable encryption at rest (KMS) and in transit (TLS 1.2+) for all PHI — this demo already demonstrates this pattern
4. **Access Controls**: Implement identity federation, MFA, and audit logging — this demo demonstrates RBAC patterns but not full HIPAA-grade controls
5. **Audit Logging**: Enable AWS CloudTrail, VPC Flow Logs, and S3 access logging for all PHI access
6. **Data Retention**: Implement retention policies per applicable regulations (typically 6-10 years for medical records)
7. **Minimum Necessary**: Restrict PHI access to the minimum data needed for each function
8. **Incident Response**: Establish breach notification procedures. The HIPAA Breach Notification Rule requires notifying affected individuals without unreasonable delay and in no case later than 60 calendar days after discovery ([45 CFR 164.404](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-D/section-164.404)); other regulations (for example GDPR) can set shorter deadlines
9. **Replicated PHI**: The data lake and audit-log buckets replicate to `replica_region` (default `us-west-2`; see `infra/replication.tf`). Replicas are PHI: keep the replica Region in the same jurisdiction, include it in your BAA and data-residency scope, and apply the same access controls there. Deletes replicate too; the versioned source buckets keep prior versions recoverable.

---

## FDA Regulatory Pathway

Clinical AI/ML software may be regulated as a Software as a Medical Device (SaMD):

- **Clinical Decision Support (CDS)**: If the software is intended to inform clinical decisions, it may require FDA oversight
- **Risk Classification**: Determined by the intended use and severity of condition (Class I/II/III)
- **Predetermined Change Control Plan**: FDA guidance for ML models that evolve over time
- **Good Machine Learning Practice (GMLP)**: FDA/Health Canada/MHRA joint principles

**This demo is NOT intended as a medical device and does not require FDA clearance.** It demonstrates architectural patterns only.

---

## What This Demo DOES Demonstrate (Relevant to Compliance)

While not compliant itself, this demo shows patterns that align with regulatory expectations:

| Pattern | Compliance Relevance |
|---|---|
| Bias metrics (CI, DPL, AD) in a SageMaker AI Processing job, LLM evaluation with Amazon Bedrock Evaluations | Algorithmic fairness requirements, health equity |
| SHAP explainability (SHAP library) | FDA transparency expectations for AI/ML |
| RBAC enforced by IAM (Cedar policies illustrative only) | HIPAA minimum necessary, role-based access |
| AgentCore Gateway guardrail policies deny prompt attacks, harmful content and direct identifiers before the agent runs | HIPAA minimum necessary, input safety |
| Confidence thresholds + human review | FDA human-in-the-loop requirements |
| Audit trail (S3 + CloudWatch) | HIPAA audit requirements |
| Override workflow | Clinical accountability, physician authority |
| Drift detection | Post-market surveillance, model monitoring |
| Automated Reasoning guardrails | Safety validation, logical consistency |

---

## Recommendations for Production Deployment

If extending this demo toward production healthcare use:

1. **Engage legal counsel** specializing in healthcare regulatory compliance
2. **Conduct a risk assessment** per ISO 14971 (risk management for medical devices)
3. **Implement a Quality Management System** (ISO 13485 or equivalent)
4. **Perform clinical validation** with real clinical data under IRB oversight
5. **Execute a BAA with AWS** before any PHI touches the infrastructure
6. **Enable AWS CloudTrail** for complete API-level audit trails
7. **Add AWS Config rules** for continuous compliance monitoring
8. **Implement data classification** to tag and track PHI throughout the pipeline
9. **Add network segmentation** beyond what this demo provides (dedicated VPCs, PrivateLink)
10. **Conduct penetration testing** and security assessment before go-live

---

## References

- [AWS HIPAA Compliance](https://aws.amazon.com/compliance/hipaa-compliance/)
- [AWS HIPAA Eligible Services](https://aws.amazon.com/compliance/hipaa-eligible-services-reference/)
- [AWS Well-Architected Healthcare Lens](https://docs.aws.amazon.com/wellarchitected/latest/healthcare-industry-lens/healthcare-industry-lens.html)
- [FDA Digital Health Software Precertification](https://www.fda.gov/medical-devices/digital-health-center-excellence)
- [FDA AI-Enabled Medical Devices](https://www.fda.gov/medical-devices/artificial-intelligence-enabled-medical-devices/list-artificial-intelligence-enabled-medical-devices)
- [GMLP Guiding Principles](https://www.fda.gov/medical-devices/artificial-intelligence-enabled-medical-devices/good-machine-learning-practice-medical-device-development-guiding-principles)
