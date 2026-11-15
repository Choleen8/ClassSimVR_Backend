# ClassSim VR Backend and Shared Integration Design

## Intent

Implement the backend responsibilities from the ClassSim VR proposal and define stable shared contracts for the future Unreal client. The backend owns session state, class/persona generation, behavior orchestration, persistence, privacy metadata, recording intake, assessment orchestration, PPST/BTI report data, and integration-safe APIs. Unreal owns VR presentation and device-specific capture/rendering.

## Goals

- Generate a different session-only class of 15–30 simulated students.
- Automatically assign generated students to the current session.
- Support session start, reset, pause, resume, and completion semantics.
- Persist session metadata, consent, turns, recordings, and assessment/report state.
- Keep live learner behavior separate from post-session multimodal assessment.
- Add explicit PPST/BTI rubric and explainable evidence contracts.
- Provide HTTP JSON contracts that Unreal can consume through an adapter.
- Provide a transport-neutral event model that can later support WebSocket/realtime delivery.
- Bound OpenAI input/output and provide fallback behavior under degraded connectivity.
- Document the exact Unreal-to-backend responsibilities and lifecycle.

## Non-goals for this backend milestone

- Building Unreal Blueprints, C++ gameplay code, MetaHuman assets, or VR scenes.
- Implementing headset-specific STT/TTS/lip-sync SDKs inside the Python service.
- Claiming an AI assessment is validated as an official teacher evaluation.
- Calling OpenAI to invent roster identities; local deterministic generation is required first.

## Architecture

The service remains a FastAPI + SQLAlchemy backend targeting PostgreSQL. Domain services own state transitions and validation; routes expose Pydantic DTOs; provider adapters isolate OpenAI, storage, and assessment integrations. Tests use SQLite and fakes. The live behavior path is low-latency and token-bounded. The assessment path is asynchronous and recording-grounded.

## Core lifecycle

```text
create session
  -> give consent before recording
  -> generate 15..30 session-only students
  -> Unreal loads roster and spawns visual learners
  -> start live run
  -> send trainee/scene events and receive learner behavior
  -> persist turns and recording metadata
  -> stop recording and complete run
  -> queue recording-based assessment
  -> publish PPST/BTI report with evidence
  -> reset or archive session according to retention policy
```

## Shared boundary decisions

- HTTP JSON is the first integration transport; route names and DTOs are versioned under `/api/v1`.
- Unreal never receives database sessions, ORM objects, provider response objects, or secret keys.
- The backend accepts trainee text/scene events; Unreal or a speech provider may supply STT text from headset audio.
- The backend returns learner dialogue, emotion, action, and fallback status; Unreal handles voice, animation, and rendering.
- Recording upload is asynchronous-capable. The backend stores metadata and accepts an adapter for object storage rather than assuming local disk in production.
- Assessment receives a recording reference and rubric selection, not a live transcript as the authoritative evidence source.

## Session-only roster policy

- Generated students are persisted while the session is active so state and turns can be recovered.
- They are marked as session-generated and owned by one session.
- Global reusable-persona listings exclude them.
- A second generation request for the same active session returns `409 Conflict`.
- Reset/completion applies the configured retention policy; it must not delete evidence needed by an assessment/report.
- Generation uses a local seed and bounded catalogs, so it consumes no OpenAI tokens.

## Assessment boundary

The backend implements rubric configuration, assessment job lifecycle, provider input/output contracts, validation, and report persistence. A provider adapter can initially return a deterministic fake or controlled unavailable status. Production multimodal model selection and research validation remain deployment decisions.

## Privacy boundary

Consent is required before recording start. Recording metadata must include lifecycle status, timestamps, retention/deletion state, and storage reference without exposing raw secrets. Access control and institution roles are required before production deployment.
