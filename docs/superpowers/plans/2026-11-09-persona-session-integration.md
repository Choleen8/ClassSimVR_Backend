# ClassSim VR Persona and Unreal Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable simulated-learner personas, per-session persona state, and stable versioned HTTP endpoints for Unreal integration.

**Architecture:** Extend the existing SQLAlchemy models with JSON-safe persona configuration and session runtime state. Add a service layer that owns persona assignment/update rules, Pydantic DTOs for the HTTP contract, and `/api/v1` routes that use injected database sessions.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLAlchemy, PostgreSQL production target, SQLite test database.

**Spec:** `docs/superpowers/specs/2026-11-09-persona-session-integration-design.md`

## Global Constraints

- `TRAINEE` remains the real pre-service teacher; `PERSONA` remains a simulated learner.
- `session_id` remains an integer.
- Existing `/sessions/{session_id}/turn` behavior endpoint remains available.
- New Unreal-facing routes use `/api/v1` and JSON DTOs.
- Tests must not require PostgreSQL, OpenAI, or Unreal.

## Review Focus

- Duplicate persona assignment to one session must be rejected; test composite-key behavior.
- Runtime state updates must not mutate the reusable persona profile; test persisted separation.
- Unknown persona/session IDs must produce controlled 404 responses.
- Unreal-facing response fields must be JSON-serializable and stable; test OpenAPI and route payloads.
- Oversized persona text and state payloads must be rejected or bounded before use; test validation limits.

### Task 1: Persona and session-state ORM fields

**Files:**
- Modify: `src/classsimvr/models/reference.py`
- Modify: `src/classsimvr/models/interaction.py`
- Modify: `tests/test_core_models.py`
- Create: `tests/test_persona_state_models.py`

- [ ] Write failing tests for persona configuration columns and session-persona runtime-state columns.
- [ ] Run the focused tests and observe failure.
- [ ] Implement portable JSON columns and bounded scalar fields.
- [ ] Run focused tests and the existing model suite.

### Task 2: Persona/session DTOs and service layer

**Files:**
- Create: `src/classsimvr/services/personas.py`
- Create: `src/classsimvr/services/persona_contracts.py`
- Create: `tests/test_persona_service.py`

- [ ] Write failing tests for create/list/assign/update behavior, duplicate assignment, and missing records.
- [ ] Run focused tests and observe failure.
- [ ] Implement `PersonaService` with injected SQLAlchemy session.
- [ ] Run focused tests and verify persistence using SQLite.

### Task 3: Versioned Unreal-facing API

**Files:**
- Create: `src/classsimvr/routes/personas.py`
- Create: `src/classsimvr/routes/health.py`
- Modify: `src/classsimvr/app.py`
- Modify: `src/classsimvr/routes/__init__.py`
- Create: `tests/test_persona_routes.py`

- [ ] Write failing tests for health, persona CRUD/list, assignment, state update, validation, and 404 responses.
- [ ] Run focused tests and observe failure.
- [ ] Implement `/api/v1` routes with database dependency overrides for tests.
- [ ] Verify OpenAPI includes stable response schemas.

### Task 4: Behavior-service integration contract

**Files:**
- Modify: `src/classsimvr/routes/behavior.py`
- Modify: `src/classsimvr/services/contracts.py`
- Create: `tests/test_persona_behavior_integration.py`

- [ ] Write failing tests for resolving an assigned persona into a bounded behavior request.
- [ ] Run focused tests and observe failure.
- [ ] Add an optional versioned behavior route that accepts only session/persona IDs plus trainee input and loads state through the service layer.
- [ ] Verify the legacy behavior route remains compatible.

### Task 5: Final integration verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `src/classsimvr/app.py`

- [ ] Document the Unreal HTTP contract, base URL, response shapes, and caching expectations.
- [ ] Run the complete test suite, syntax compilation, metadata creation, and OpenAPI checks.
- [ ] Confirm no secrets, generated caches, or Unreal-specific assumptions are added to the backend.
