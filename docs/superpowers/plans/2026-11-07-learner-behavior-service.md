# ClassSim VR Learner-Behavior Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a token-bounded OpenAI learner-behavior service and a minimal FastAPI turn endpoint.

**Architecture:** Keep prompt construction and token trimming in small pure helpers, isolate OpenAI client creation behind `openai_client.py`, and make `BehaviorService` accept an injected client. The HTTP route delegates to the service and does not know OpenAI request details.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, OpenAI Python SDK, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-learner-behavior-service-design.md`

## Global Constraints

- Default behavior budget is at most 1,200 estimated input tokens and 180 output tokens.
- No network calls in automated tests.
- Do not log API keys, raw prompts, raw recordings, or complete session transcripts.
- Do not implement the assessment engine in this slice.
- Do not require a live PostgreSQL database for the service tests.

## Review Focus

- Oversized context must be trimmed before the OpenAI call; test the request payload size.
- Malformed model JSON must become a controlled service error or fallback, not an uncaught parser crash.
- OpenAI transient failures must retry only within a bounded limit; test call count.
- Missing `OPENAI_API_KEY` must fail at client construction without exposing secrets.
- The route must validate empty input and return the service’s structured response.

### Task 1: Token budget and request/response contracts

**Files:**
- Create: `src/classsimvr/services/token_budget.py`
- Create: `src/classsimvr/services/contracts.py`
- Create: `tests/test_behavior_contracts.py`

**Interfaces:**
- Produces `TokenBudget`, `BehaviorRequest`, `BehaviorResponse`, and `TokenUsage`.

- [ ] Write failing tests for bounded context preparation, request validation, response validation, and fallback response shape.
- [ ] Run `pytest tests/test_behavior_contracts.py -v` and observe failure because modules do not exist.
- [ ] Implement the pure contracts and approximate token-budget helper.
- [ ] Run the focused tests and verify they pass.

### Task 2: OpenAI adapter and behavior service

**Files:**
- Create: `src/classsimvr/services/openai_client.py`
- Create: `src/classsimvr/services/behavior.py`
- Create: `tests/test_behavior_service.py`

**Interfaces:**
- Consumes `BehaviorRequest`, `BehaviorResponse`, and `TokenBudget`.
- Produces `BehaviorService.generate_reply(request)`.

- [ ] Write failing tests for one bounded Responses API call, structured parsing, usage extraction, retry limits, and fallback behavior.
- [ ] Run the focused tests and observe failure.
- [ ] Implement lazy client creation, prompt construction, bounded retries, and fallback.
- [ ] Run the focused tests and verify they pass.
- [ ] Run the full test suite.

### Task 3: FastAPI route integration

**Files:**
- Create: `src/classsimvr/routes/behavior.py`
- Create: `src/classsimvr/app.py`
- Modify: `src/classsimvr/services/__init__.py`
- Create: `tests/test_behavior_route.py`

- [ ] Write failing tests for valid turn requests and empty-input validation.
- [ ] Run the focused tests and observe failure.
- [ ] Implement the route and app factory with dependency injection for `BehaviorService`.
- [ ] Run the focused tests and verify they pass.

### Task 4: Packaging and final verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `src/classsimvr/__init__.py`

- [ ] Add runtime dependencies to the project metadata so installation matches `requirements.txt`.
- [ ] Document environment configuration and token-budget behavior without including secret values.
- [ ] Run `pytest -q` from the project root.
- [ ] Run an import check for the FastAPI app without making an OpenAI request.
- [ ] Inspect the final file list and confirm no secrets or test artifacts were added.
