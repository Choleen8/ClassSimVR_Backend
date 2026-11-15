# WebSocket Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WebSocket transport for real-time learner turns while preserving every existing HTTP route and response contract.

**Architecture:** Add a dedicated FastAPI WebSocket route at `/api/v1/ws/sessions/{session_id}`. The route will accept bounded JSON messages, reuse the existing database persona validation and behavior/TTS services, and return explicit JSON events. HTTP remains the source-compatible fallback and is not modified.

**Tech Stack:** FastAPI WebSocket, Pydantic, existing SQLAlchemy persona/session services, existing behavior and TTS services, pytest.

**Spec:** Additive WebSocket transport approved in conversation on 2026-11-16.

## Global Constraints

- Existing HTTP routes, paths, request models, and response models must remain unchanged.
- WebSocket messages must be bounded and JSON-only.
- The server must reject unassigned personas before invoking the behavior provider.
- Provider/TTS failures must return a structured error or fallback response without crashing the connection.
- No new external dependency is required.

## Review Focus

- Invalid JSON or unsupported message types must return a structured error and keep the socket usable.
- Oversized or invalid turn payloads must be rejected before provider calls.
- Unknown sessions and personas must be rejected without leaking database details.
- Disconnects must not produce unhandled server errors.
- The existing HTTP OpenAPI contract and tests must remain unchanged.

---

### Task 1: WebSocket message contracts

**Files:**
- Create: `src/classsimvr/services/websocket_contracts.py`
- Test: `tests/test_websocket_contracts.py`

**Interfaces:**
- Produce bounded `WebSocketTurnMessage` and serializable event helpers for `connected`, `turn_response`, `error`, and `pong` events.

- [ ] Write failing tests for valid turn messages, ping messages, unsupported types, and field limits.
- [ ] Run the focused contract tests and confirm they fail because the module does not exist.
- [ ] Implement the Pydantic contracts with a maximum message size enforced by field limits.
- [ ] Run the focused contract tests and confirm they pass.

### Task 2: WebSocket route

**Files:**
- Create: `src/classsimvr/routes/websocket.py`
- Modify: `src/classsimvr/app.py`
- Test: `tests/test_websocket_routes.py`

**Interfaces:**
- Add `GET`-style WebSocket handshake route `/api/v1/ws/sessions/{session_id}`.
- Receive `{"type":"turn","persona_id":123,"trainee_input":"...","scene_state":"...","recent_turns":[]}`.
- Receive `{"type":"ping"}`.
- Return `connected`, `turn_response`, `error`, and `pong` event objects.

- [ ] Write failing route tests for connection acknowledgement, ping/pong, assigned-persona turn response, invalid persona rejection, and malformed messages.
- [ ] Run the focused route tests and confirm they fail because the route is not registered.
- [ ] Implement the route by reusing current services and closing only on protocol-level failure or disconnect.
- [ ] Register the new router without changing existing routers.
- [ ] Run the focused route tests and confirm they pass.

### Task 3: Integration verification

**Files:**
- Modify: `README.md`
- Modify: `docs/integration/UNREAL_BACKEND_INTEGRATION.md`

- [ ] Document the WebSocket URL, message examples, event types, reconnect behavior, and HTTP fallback.
- [ ] Run the complete test suite.
- [ ] Verify the HTTP OpenAPI contract tests still pass.
- [ ] Verify no existing HTTP route files were changed.
