# ClassSim VR - AI Coding Agent Context

This file is the working context for AI coding agents operating in this repository. Read it before changing code. Keep this document synchronized with the implementation when architecture or product scope changes.

## 1. Product identity and research purpose

ClassSim VR is an AI-integrated virtual-reality teaching simulation for Philippine pre-service teachers. It gives a trainee a safe, repeatable classroom in which to practice instruction and classroom management before or during Field Study and Teaching Internship.

The research goal is not to build a generic chatbot or game. The system must rehearse situated teaching behavior in a realistic Philippine classroom and produce explainable performance evidence aligned to selected Philippine Professional Standards for Teachers (PPST) Beginning Teacher Indicators (BTIs).

The proposal is based on the project paper titled **ClassSim VR: An AI-Integrated Virtual Reality Teaching Simulation with PPST-Anchored Assessment for Philippine Pre-Service Teacher Training**. The source proposal PDF was supplied as `EduVerse - Google Docs.pdf`. Treat the paper's pedagogical purpose, Philippine context, PPST alignment, privacy requirements, and declared delimitations as product requirements.

## 2. Core features from the paper

These are the product's core features. They are the north star for future implementation and should not be silently removed or replaced with unrelated functionality.

1. **Immersive Philippine classroom environment**
   - Unreal Engine VR client.
   - OpenXR and Meta Quest compatibility are target technologies.
   - The classroom should represent realistic density, seating, movement, gaze, and spatial constraints found in Philippine basic-education settings.

2. **AI-driven virtual learner characters**
   - Filipino learner avatars are driven by reusable persona configurations.
   - Dialogue is generated in character instead of relying only on fixed dialogue branches.
   - The backend must return structured dialogue, emotion, and action data that Unreal can render.

3. **Multilingual and code-switching support**
   - Personas have a language profile.
   - The intended language space is English, Filipino, and code-switched Taglish.
   - Live speech-to-text affects the real-time behavior loop only; it must not become the sole evidence source for assessment.

4. **Persona and behavior-state system**
   - A reusable `PERSONA` stores learner identity and stable traits.
   - A `SESSION_PERSONA` stores per-session mutable state such as engaged, confused, disengaged, or disruptive behavior, current emotion, and memory summary.
   - Runtime state must not mutate the reusable persona profile.
   - State must remain consistent through session memory and must be bounded before entering an LLM request.

5. **Scalable class density**
   - The current backend class-generation range is 15–30 students per session.
   - The visual class may represent a full-size Philippine class.
   - Only a configured foreground subset should require active persona/LLM behavior in real time.
   - Ambient learners should remain local, scripted, or otherwise inexpensive until they need active behavior.
   - Do not design a system that assumes one LLM call per visible learner per frame or per event.

6. **Real-time voice interaction and lip-sync**
   - Target pipeline: headset microphone -> speech-to-text -> behavior service -> learner dialogue/emotion/action -> text-to-speech and facial/lip animation.
   - TTS is implemented through the local Edge TTS adapter; speech-to-text, facial animation, lip-sync, and Unreal integration remain client-side/future work.
   - Keep the HTTP contract transport-neutral so a future WebSocket or realtime voice adapter can be added without changing domain models.

7. **MATATAG/K-12-aligned scenario configuration**
   - Scenarios must support grade level, lesson plan, competency, answer-key reference, class size, and difficulty.
   - The lesson/scenario should be compatible with MATATAG and K-12 learning competencies.

8. **Full session recording**
   - The intended system records audio and video, live transcript/timing, and trainee actions.
   - Recording is the direct evidence source for post-session assessment.
   - Consent, retention, deletion, and access control are mandatory design concerns because recordings are personal data.

9. **Recording-based multimodal assessment**
   - After the session, a separate assessment engine should inspect the complete audio-video recording directly.
   - Assessment must not depend only on the live STT transcript.
   - The assessment model may be slower and heavier than the real-time behavior model.

10. **Explainable PPST/BTI-mapped performance report**
    - Reports must include per-indicator scores, cited supporting moments, an overall summary, and a suggested next focus.
    - The report is supplementary evidence for trainees, college supervisors, and cooperating teachers; it does not replace human professional judgment.

11. **Repeatable practice runs**
    - A trainee must be able to reset and replay the same scenario with a different approach.
    - Session state, recording, report, and persona runtime state must have clear run boundaries.

12. **Offline-tolerant operation**
    - Live behavior needs bounded retries and a local cached fallback response set when connectivity is degraded.
    - Assessment is asynchronous after recording upload and should be queueable rather than blocking the live session.

13. **Privacy-compliant data handling**
    - Design for the Philippine Data Privacy Act of 2012 (Republic Act No. 10173) and institutional research-ethics requirements.
    - Consent must be explicit before recording.
    - Avoid logging API keys, raw sensitive recordings, or unnecessary personal data.
    - Retention, deletion, restricted access, and auditability must be explicit before production deployment.

## 3. PPST assessment dimensions in the paper

The planned assessment rubric uses selected Beginning Teacher Indicators. Preserve these mappings when implementing scoring or reports:

| Assessment dimension | PPST mapping | BTI |
|---|---|---|
| Content delivery and accuracy | Domain 1, Strand 1.1 | 1.1.1 |
| Questioning and higher-order thinking | Domain 1, Strand 1.5 | 1.5.1 |
| Language use and code-switching | Domain 1, Strand 1.6 | 1.6.1 |
| Verbal and non-verbal communication | Domain 1, Strand 1.7 | 1.7.1 |
| Fair, respectful, and caring climate | Domain 2, Strand 2.2 | 2.2.1 |
| Classroom structure and activity management | Domain 2, Strand 2.3 | 2.3.1 |
| Learner participation and engagement | Domain 2, Strand 2.4 | 2.4.1 |
| Promotion of purposive learning | Domain 2, Strand 2.5 | 2.5.1 |
| Management of learner behavior | Domain 2, Strand 2.6 | 2.6.1 |
| Responsiveness to learner diversity | Domain 3, Strand 3.1 | 3.1.1 |

Assessment output should use these standards as structured data, not as unvalidated free-form model text.

## 4. Current implementation status

### Implemented now

- Python 3.12+ backend package under `src/classsimvr`.
- FastAPI application in `src/classsimvr/app.py`.
- SQLAlchemy ORM model layer designed for PostgreSQL.
- Database dependency in `src/classsimvr/database.py` using `DATABASE_URL`.
- EERD-derived models for trainees, sessions, personas, session-persona assignments, turns, consent, recordings, reports, BTIs, score subtypes, and evidence.
- Reusable persona fields: language, academic baseline, avatar reference, personality traits, behavior triggers, response style, participation level, and active flag.
- Per-session persona runtime fields: initial/current state, current emotion, state summary, JSON state data, and active flag.
- Persona service layer with creation, global listing, trainee-level aggregation, session assignment, session listing, and runtime-state updates.
- Versioned API health, persona, trainee-persona, session-persona, and behavior routes under `/api/v1`.
- Session lifecycle routes for start, pause, resume, complete, and reset.
- Deterministic local generation of 15–30 session-only students with automatic assignment and reset cleanup.
- Consent and recording metadata/intake routes with a required-consent guard.
- Consent purpose/notice-version tracking, withdrawal, and a guard that blocks new recording after withdrawal.
- Withdrawal also marks the session `not_gradable` without stopping the lesson; assessment returns a stable `recording_consent_withdrawn` code and message.
- Philippine privacy/compliance baseline and privacy-notice template under `docs/compliance/`.
- Turn-ingestion route that validates session/persona assignment before persistence.
- PPST/BTI rubric endpoint and validated asynchronous assessment-job queue/status contract.
- Versioned Unreal behavior route:
  `POST /api/v1/sessions/{session_id}/personas/{persona_id}/turn`
  The client sends trainee input and optional scene/history context; the server resolves the assigned persona and runtime state.
- Legacy behavior route remains available:
  `POST /sessions/{session_id}/turn`
- OpenAI Responses API behavior generation with strict structured output, bounded input/output tokens, bounded retries, and local fallback behavior.
- Tests use SQLite and fake OpenAI clients; tests do not require PostgreSQL, OpenAI network access, or Unreal.
- Persona/profile and runtime JSON validation, inactive-persona protection, wrong-persona response protection, and read-only convenience many-to-many relationships.

### Not implemented yet

Do not describe these as complete merely because ORM tables or placeholder models exist:

- Unreal Engine client, OpenXR, MetaHuman rendering, HTTP audio playback adapter, speech-to-text, lip-sync, facial animation, or WebSocket transport. Edge TTS generation and HTTP audio serving are implemented.
- Production hardware validation for the real-time STT pipeline (the backend
  push-to-talk microphone endpoints, local faster-whisper adapter, and dynamic
  student registry are implemented; Quest recording still needs an end-to-end
  hardware test).
- Production recording upload/storage/deletion adapter and signed object-storage workflow.
- Asynchronous assessment worker/provider execution over the stored audio-video recording.
- Production multimodal provider execution over the stored audio-video recording.
- Authentication, authorization, institution/role boundaries, and supervisor access controls.
- Database migrations, seed data, production PostgreSQL deployment, queues, observability, and rate limiting.
- Frontend screens. The current project is primarily a backend service.

## 5. EERD/domain model rules

Use the EERD and `src/classsimvr/models/EERD_RELATIONSHIPS.md` as the source of truth for domain relationships.

- `TRAINEE` is the real pre-service teacher using the simulation.
- `SESSION` belongs to one trainee and represents one practice run.
- `PERSONA` is a reusable simulated learner profile, not the real trainee.
- `SESSION_PERSONA` assigns a persona to one session and owns mutable runtime state.
- `TURN` logs a learner interaction for a session and persona.
- `BTI` is a reference standard selected for a session and measured by indicator scores.
- `CONSENT`, `RECORDING`, and `REPORT` are session-owned records.
- `INDICATOR_SCORE` belongs to a report and BTI; `OBSERVED_SCORE` and `NOT_OBSERVED` are disjoint score subtypes.
- `EVIDENCE` belongs to an observed score and should cite recording moments.
- Use the association objects (`SessionPersona`, `SessionBti`) for writes. Convenience many-to-many relationships are read-only.
- Do not put mutable session state on `Persona`.
- Preserve integer database IDs and stable JSON DTOs for Unreal.

## 6. Current API contract

Base URL in local development is normally `http://127.0.0.1:8000`.

```text
GET    /api/v1/health
POST   /api/v1/sessions
POST   /api/v1/sessions/{session_id}/students/generate
POST   /api/v1/sessions/{session_id}/start
POST   /api/v1/sessions/{session_id}/pause
POST   /api/v1/sessions/{session_id}/resume
POST   /api/v1/sessions/{session_id}/complete
POST   /api/v1/sessions/{session_id}/reset
POST   /api/v1/personas
GET    /api/v1/personas
GET    /api/v1/trainees/{trainee_id}/personas
POST   /api/v1/sessions/{session_id}/personas
GET    /api/v1/sessions/{session_id}/personas
PATCH  /api/v1/sessions/{session_id}/personas/{persona_id}
POST   /api/v1/sessions/{session_id}/personas/{persona_id}/turn
POST   /api/v1/sessions/{session_id}/turns
PUT    /api/v1/sessions/{session_id}/consent
DELETE /api/v1/sessions/{session_id}/consent
POST   /api/v1/sessions/{session_id}/recording/start
POST   /api/v1/sessions/{session_id}/recording/complete
GET    /api/v1/assessment/rubric
POST   /api/v1/sessions/{session_id}/assessment
GET    /api/v1/assessment/jobs/{job_id}
POST   /api/v1/assessment/jobs/{job_id}/result
GET    /api/v1/sessions/{session_id}/report
POST   /sessions/{session_id}/turn       # legacy compatibility route
```

The Unreal-facing behavior request is intentionally small:

```json
{
  "trainee_input": "What is one half?",
  "scene_state": "The teacher is demonstrating fractions.",
  "recent_turns": []
}
```

The structured response is:

```json
{
  "persona_id": "12",
  "dialogue": "Can you show me another example?",
  "emotion": "curious",
  "action": "raises_hand",
  "fallback": false,
  "usage": null
}
```

Unreal should use a small API adapter and never depend on SQLAlchemy names, database sessions, internal prompt text, or provider-specific response objects. Cache the assigned persona list for a session when appropriate, but refresh runtime state from the session endpoint when state synchronization matters.

## 7. OpenAI and token-management rules

- Keep behavior generation and assessment as separate services. They have different latency, model, prompt, and evidence requirements.
- Do not send the complete database object or unbounded conversation history to OpenAI.
- Keep persona profile text compact and stable.
- Bound scene state, summary, recent turns, and trainee input before the model call.
- Keep output schema strict: persona ID, dialogue, emotion, and action.
- Enforce that the model-returned persona ID matches the requested persona ID.
- Use bounded retry counts and a deterministic local fallback.
- Never expose provider errors, API keys, or raw prompts in API responses.
- Treat the compliance documents as an engineering baseline, not legal certification. Production deployment requires a named PIC/DPO, approved notice and lawful basis, privacy impact assessment, retention/deletion workflow, vendor/transfer review, security controls, and incident-response process.
- Tests must use fake clients and must not make live OpenAI calls.

## 8. Development and testing workflow

From the project root:

```powershell
uvicorn classsimvr.app:app --reload
python -m pytest -q
```

The repository's `pyproject.toml` adds `src` to pytest's Python path. The production database target is PostgreSQL through `DATABASE_URL`; tests should use SQLite fixtures and dependency overrides.

Before changing behavior:

1. Read this file and the relevant design/spec files in `docs/superpowers`.
2. Inspect existing models, service contracts, routes, and tests.
3. Write a failing focused test first.
4. Implement the smallest change that satisfies the test and the paper's architecture.
5. Run focused tests, then the complete suite.
6. Verify OpenAPI routes and response schemas for public API changes.
7. Review privacy, token cost, fallback, and Unreal compatibility implications.

Do not rewrite unrelated files, rename public routes casually, add provider-specific types to the Unreal contract, or claim a paper feature is complete without an implementation and test.

## 9. Recommended implementation order

When continuing development, prefer this order:

1. Add scenario configuration for MATATAG/K-12 competency, lesson plan, answer key, class size, and difficulty.
2. Implement the Unreal HTTP adapter separately from this backend.
3. Add production recording upload/storage/deletion and an asynchronous multimodal assessment worker.
4. Add final PPST/BTI score/report persistence with evidence citations.
5. Add authentication, roles, institution boundaries, observability, rate limiting, and production migrations.

Every milestone should preserve the distinction between real-time learner behavior and post-session recording-based assessment.

## 10. Safety and research integrity

- Do not treat an AI score as an official teacher evaluation without human review and research validation.
- Preserve cited evidence for every assessment score.
- Make model uncertainty, fallback responses, missing recordings, and unavailable assessment explicit in status fields.
- Do not collect or expose more trainee/learner data than the research protocol requires.
- Keep secrets in `.env` or the deployment secret manager; never commit them or print them.
- Do not make destructive database or filesystem changes without verifying exact targets.
