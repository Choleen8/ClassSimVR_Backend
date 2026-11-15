# ClassSim VR Persona and Unreal Integration Design

## Intent

Give every simulated learner a reusable persona and a separate per-session runtime state, then expose stable JSON APIs that an Unreal client can call over HTTP without knowing the database schema.

## Goals

- Expand `PERSONA` with language, personality, behavior triggers, response style, participation, and avatar metadata.
- Expand `SESSION_PERSONA` with mutable session state without mutating the reusable persona.
- Provide versioned persona and session-persona endpoints under `/api/v1`.
- Keep response DTOs stable and explicit for Unreal integration.
- Make the behavior endpoint use persona/session state in later integration while preserving the existing endpoint.
- Add health and OpenAPI discoverability.
- Test with SQLite and fake behavior services; do not require PostgreSQL or OpenAI for tests.

## Non-goals

- Unreal Engine code or WebSocket/voice integration.
- Authentication and institution-level authorization.
- Recording upload and post-session assessment.
- Automatic migrations; schema changes are represented in ORM metadata and tests for now.

## Domain decisions

- `TRAINEE` means the real pre-service teacher using the simulation.
- `PERSONA` means a reusable simulated learner profile.
- `SESSION_PERSONA` means one persona assigned to one session, with mutable state such as emotion, behavior state, and summary.
- `session_id` remains an integer to match the existing database model and API path.
- Unreal receives JSON IDs, status codes, and DTOs; it never receives database sessions or ORM objects.
- API versioning is additive: existing `/sessions/{session_id}/turn` remains available, while new integration endpoints use `/api/v1`.

## Unreal integration contract

- `GET /api/v1/health` returns service status and API version.
- `POST /api/v1/personas` creates a reusable persona.
- `GET /api/v1/personas` lists available personas.
- `POST /api/v1/sessions/{session_id}/personas` assigns a persona to a session.
- `GET /api/v1/sessions/{session_id}/personas` returns assigned personas and current state.
- `PATCH /api/v1/sessions/{session_id}/personas/{persona_id}` updates runtime state.
- All routes use Pydantic DTOs with explicit fields and JSON-safe values.

## Performance and token decisions

- Only active foreground personas should call the AI behavior service.
- Ambient learners remain local or scripted until an active response is needed.
- Persona profile text is stable and compact; session state and recent turns are bounded before each model call.
- Unreal can request the assigned persona list once and cache it for the session.
