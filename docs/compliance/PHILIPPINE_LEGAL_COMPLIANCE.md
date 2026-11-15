# ClassSim VR Philippine Legal and Compliance Baseline

> Engineering baseline only; not legal advice or a certification. Before real recordings or school deployment, obtain approval from the institution's PIC, Data Protection Officer, research/ethics body, and legal adviser.

## Scope

ClassSim VR may process trainee identity/contact data, speech or transcripts, optional audio/video, VR interactions, observations, scores, reports, and AI-generated feedback. Synthetic personas are fictional, but data about a real trainee or learner remains personal data. Real minors or school learners require additional child-protection and age-appropriate safeguards.

## Primary references

- [Republic Act No. 10173 / Data Privacy Act](https://privacy.gov.ph/data-privacy-act/)
- [NPC Guidelines on Consent](https://privacy.gov.ph/wp-content/uploads/2024/05/2023-compendium-2.pdf)
- [NPC Data Subject Rights](https://privacy.gov.ph/data-subject-rights/)
- [NPC Advisory on AI systems processing personal data](https://privacy.gov.ph/wp-content/uploads/2025/02/Advisory-2024.12.19-Guidelines-on-Artificial-Intelligence-w-SGD.pdf)
- [NPC child-oriented transparency guidance](https://privacy.gov.ph/wp-content/uploads/2024/10/Advisory_Guidelines_on_Child_Oriented_Transparency_For_Public_Input.pdf)
- [NPC advisories and circulars](https://privacy.gov.ph/pips-and-pics/advisories-circulars/)
- [DepEd study-request example](https://region3.deped.gov.ph/services/request-to-conduct-study/)

## Accountability roles

Assign a Personal Information Controller (PIC), any processors/vendors (PIPs), a Data Protection Officer, a research/ethics lead, and a system/security owner before deployment. The institution—not this codebase—must determine the lawful basis, purposes, retention, notices, contracts, and response process.

## Processing inventory and safeguards

| Data | Purpose | Minimum safeguards |
|---|---|---|
| Trainee name/email | Account and session ownership | Minimize; restrict access; publish retention |
| Speech/transcript | Simulation and feedback | Notice purpose; avoid raw text in logs |
| Audio/video | Optional research/review evidence | Separate consent; secure storage; deletion workflow |
| VR actions and scores | Formative assessment/research | Explain metrics; human review; no sole high-impact decision |
| AI requests/responses | Persona dialogue and feedback | Minimize; vendor review; document transfers |
| Synthetic personas | Simulation content | Separate from identifiable trainee records |

## Required controls

- Document a lawful basis and legitimate purpose for every processing activity.
- Give an accessible notice identifying the PIC/DPO, data categories, purposes, providers, transfers, retention, rights, withdrawal, and complaint route.
- Use specific, informed, freely given consent where consent is the chosen basis. Keep optional recording consent separate from access to training.
- Apply transparency, legitimate purpose, proportionality, and data minimization.
- Provide workable access, correction, objection, erasure/blocking where applicable, portability where applicable, and complaint paths.
- Complete a privacy impact assessment before production processing, especially for recordings, minors, monitoring, or automated assessment.

## Implemented in this project

The API now requires a consent purpose and notice version, records when consent was given, supports withdrawal, and blocks new recording after withdrawal. This stores consent metadata only; it does not prove that the notice is approved, that a requester is authenticated, or that storage is secure. Those deployment controls remain required.

## Minors and education research

For real learners or minors, use child-appropriate explanations, the required parent/guardian or authorized-representative process, assent where appropriate, school authorization, and a safe withdrawal route. Obtain research/ethics and applicable education-institution permissions before recording. Do not use feedback as the sole basis for grades, employment, licensure, discipline, or other consequential decisions without a separately approved human-governed process.

## AI-specific safeguards

- Document model/provider, purpose, input categories, output use, retention, and transfer path.
- Never send secrets, access tokens, unnecessary identifiers, or raw recordings to prompts.
- Separate trainee behavior from fictional learner content.
- Treat generated feedback as assistive and probabilistic; require human review for consequential interpretation.
- Test relevant language, accent, disability, gender, cultural, and other performance disparities.
- Provide correction/appeal and preserve evidence for disputed results.

## Security, retention, and incidents

- Use HTTPS; encrypt databases, object storage, backups, and exports.
- Enforce authentication, least privilege, session ownership, role separation, and audit logs.
- Store API/database secrets in protected environment or a secret manager; never log or return them.
- Use signed or access-controlled recording references, not public file paths.
- Define retention for live data, backups, exports, research copies, and vendors; implement deletion/blocking consistently.
- Maintain incident response for containment, evidence, DPO escalation, affected-person communication, and required notifications.
- Review data-processing agreements and transfer terms before enabling OpenAI, hosting, transcription, analytics, or storage providers with real data.

## Pre-production checklist

- [ ] PIC, PIP, DPO, research lead, and security owner named.
- [ ] Privacy impact assessment approved.
- [ ] Privacy notice and recording consent text approved and versioned.
- [ ] Lawful basis, retention/deletion, and data-subject request workflow documented.
- [ ] AI/provider contracts and cross-border transfer review complete.
- [ ] Authentication, authorization, encryption, secret management, backups, logging, and incident response tested.
- [ ] Child/minor safeguards approved if applicable.
- [ ] Human review, appeal, and fairness testing defined for assessment outputs.
- [ ] Unreal client displays consent state and prevents recording when absent or withdrawn.
- [ ] Institution confirms any applicable NPC registration/notification or other filing.
