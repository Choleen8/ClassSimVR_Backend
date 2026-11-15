# Trainee Session History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a read-only trainee session-history endpoint for the Unreal client.

**Architecture:** Reuse the existing `SessionService`, SQLAlchemy relationships, and FastAPI route conventions. The route returns a compact history item for each session and includes report summary fields when a report exists; the existing detailed report endpoint remains unchanged.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pytest, SQLite test database.

**Spec:** `docs/superpowers/specs/2026-11-16-trainee-history.md`

## Global Constraints

- Keep existing HTTP routes unchanged.
- Do not add dependencies.
- Do not calculate or expose an overall score that the current report model does not store.
- Leave the repository uncommitted for the user to review.

## Review Focus

- Missing trainee: return 404 rather than silently returning an empty history.
- Existing trainee with no sessions: return 200 with an empty list.
- Multiple sessions: return newest first and keep sessions belonging to other trainees out.
- Missing report: return `report_available: false` and null report fields.
- Existing report: expose its summary and next focus without changing the detailed report route.

### Task 1: Add the history contract and endpoint

**Files:**
- Modify: `src/classsimvr/services/session_contracts.py`
- Modify: `src/classsimvr/services/sessions.py`
- Modify: `src/classsimvr/routes/sessions.py`
- Test: `tests/test_trainee_history_routes.py`

**Interfaces:**
- Produces `GET /api/v1/trainees/{trainee_id}/history` returning `list[TraineeHistoryResponse]`.
- Produces `SessionService.list_history(db, trainee_id) -> list[Session]`.

- [ ] Write a route test covering trainee isolation, newest-first ordering, and report summary fields.
- [ ] Run the focused test and confirm it fails because the endpoint is not implemented.
- [ ] Add the response model and service query using the existing `TraineeNotFoundError`.
- [ ] Add the route and response mapper, preserving existing routes.
- [ ] Run the focused test and then the full test suite.
