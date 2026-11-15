# ClassSim VR Backend and Shared Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the backend responsibilities and document stable shared integration contracts for the ClassSim VR Unreal client.

**Architecture:** Extend the current FastAPI/SQLAlchemy backend with focused domain services, Pydantic DTOs, and versioned routes. Keep OpenAI behavior, media storage, and assessment providers behind injectable adapters; use SQLite/fakes in tests and PostgreSQL in production.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, OpenAI Responses API, pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-backend-shared-integration-design.md`

## Global Constraints

- Session-generated class size is exactly 15–30 students.
- Generated students are session-only and excluded from reusable persona listings.
- Roster generation is local and deterministic; it must not call OpenAI.
- Existing legacy behavior routes remain compatible.
- Unreal-facing routes use explicit `/api/v1` JSON DTOs.
- Tests must not require PostgreSQL, OpenAI, Unreal, or external media storage.
- Live behavior and post-session assessment remain separate services.
- Consent is required before recording starts.

## Review Focus

- Invalid class sizes and duplicate roster generation must return controlled errors.
- Session-only students must not leak into another session or reusable persona catalog.
- Reset/completion must not delete evidence still required by assessment retention.
- Recording uploads must not expose paths, secrets, or unbounded payloads.
- Provider failure must produce explicit job/response status and not corrupt session state.

### Task 1: Session lifecycle and session-only class generation

**Files:**
- Modify: `src/classsimvr/models/core.py`
- Modify: `src/classsimvr/models/reference.py`
- Create/modify: `src/classsimvr/services/session_contracts.py`
- Create/modify: `src/classsimvr/services/sessions.py`
- Modify: `src/classsimvr/routes/personas.py` or create `src/classsimvr/routes/sessions.py`
- Test: `tests/test_session_generation.py`

**Interfaces:**
- `POST /api/v1/sessions/{session_id}/students/generate`
- Request: `{ "class_size": 15..30, "seed": optional integer }`
- Response: session ID, class size, seed, and assigned student DTOs.
- `SessionService.generate_students(db, session_id, class_size, seed) -> GeneratedClass`.

- [ ] Write failing tests for 15, 30, 14, 31, deterministic seed, automatic assignments, session isolation, and duplicate generation.
- [ ] Run focused tests and confirm they fail because the lifecycle/generator is absent.
- [ ] Add session-generation ownership metadata and deterministic local persona catalog.
- [ ] Implement the service and route with transaction rollback on partial failure.
- [ ] Run focused tests, then the existing persona suite.

### Task 2: Session lifecycle and turn persistence

**Files:**
- Modify: `src/classsimvr/models/core.py` or add a lifecycle model
- Modify: `src/classsimvr/models/interaction.py`
- Create: `src/classsimvr/services/session_lifecycle.py`
- Create/modify: `src/classsimvr/routes/sessions.py`
- Test: `tests/test_session_lifecycle.py`

- [ ] Write failing tests for create/start/pause/resume/complete/reset transitions and invalid transitions.
- [ ] Implement explicit lifecycle status and timestamps.
- [ ] Add a turn-ingestion contract that validates a generated student assignment before persisting a turn.
- [ ] Run focused and complete tests.

### Task 3: Consent and recording metadata/intake

**Files:**
- Modify: `src/classsimvr/models/interaction.py`
- Create: `src/classsimvr/services/recording_contracts.py`
- Create: `src/classsimvr/services/recordings.py`
- Create/modify: `src/classsimvr/routes/recordings.py`
- Test: `tests/test_recording_routes.py`

- [ ] Write failing tests for consent grant/rejection, recording start/stop, metadata validation, and missing consent.
- [ ] Implement injectable `RecordingStorage` interface with a test fake and metadata-only default adapter.
- [ ] Add bounded upload/reference DTOs without returning filesystem secrets.
- [ ] Run focused and complete tests.

### Task 4: PPST/BTI rubric and assessment job contracts

**Files:**
- Modify/create: `src/classsimvr/models/scoring.py`
- Create: `src/classsimvr/services/assessment_contracts.py`
- Create: `src/classsimvr/services/assessment.py`
- Create/modify: `src/classsimvr/routes/assessment.py`
- Test: `tests/test_assessment_contracts.py`

- [ ] Write failing tests for rubric indicator validation, 1–4 observed scores, not-observed scores, evidence timestamps, and report status.
- [ ] Add the ten paper-defined BTI mappings as structured seed/config data.
- [ ] Implement queued/pending/running/completed/failed assessment state and injectable provider interface.
- [ ] Persist explainable report and evidence DTOs without treating model output as trusted until validated.
- [ ] Run focused and complete tests.

### Task 5: Shared Unreal integration documentation and transport contract

**Files:**
- Create: `docs/integration/UNREAL_BACKEND_INTEGRATION.md`
- Modify: `README.md`
- Test: `tests/test_openapi_contract.py`

- [ ] Document base URL, lifecycle sequence, roster generation, behavior turn payloads, recording handoff, assessment polling, status codes, retry/idempotency expectations, and privacy rules.
- [ ] Document Unreal responsibilities versus backend responsibilities for STT, TTS, lip-sync, recording, and rendering.
- [ ] Add OpenAPI route assertions and stable response examples.
- [ ] Run full suite, compile checks, and OpenAPI validation.

### Task 6: Final review and verification

- [ ] Run `python -m pytest -q`.
- [ ] Run Python compilation and `git diff --check`.
- [ ] Verify `.env` is not tracked and no secrets appear in generated docs or logs.
- [ ] Perform an independent code review of all changed files.
- [ ] Report implemented features separately from external integrations still requiring Unreal/provider deployment.
