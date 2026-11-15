# Non-Gradable Session After Consent Withdrawal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow a lesson to continue after recording consent is withdrawn while preventing grading and returning a clear, Unreal-friendly explanation.

**Architecture:** Reuse the existing session and consent records. Consent withdrawal marks the session as non-gradable; assessment queueing checks that state and raises a stable domain error mapped to HTTP 409 with a machine-readable code and user-facing message. No live behavior route is blocked.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic, SQLite test fixtures, pytest.

**Spec:** User-approved policy in the conversation: “Recording consent was withdrawn; this session cannot be graded.”

## Global Constraints

- The lesson/session must continue after withdrawal.
- New recording must remain blocked after withdrawal.
- No assessment job or grade may be created after withdrawal.
- The API must expose a stable status/code/message for Unreal.
- Preserve the distinction between formative live behavior and recording-based assessment.

## Review Focus

- Withdrawal before any recording: assessment is rejected with the same explicit non-gradable message.
- Withdrawal after a completed recording: an existing recording cannot be used to create a grade.
- Withdrawal during a running lesson: behavior/session routes remain usable.
- Repeated assessment requests: no job is created when the session is non-gradable.
- API clients: the response includes stable `code`, `status`, and `message` fields rather than relying on free-text parsing.

### Task 1: Add the failing non-gradable assessment contract test

**Files:**
- Modify: `tests/test_assessment_contracts.py`
- Modify: `tests/test_recording_routes.py` or add a focused service test beside it

**Interfaces:**
- Consumes: existing `Consent`, `Session`, `Recording`, and `AssessmentService` behavior.
- Produces: regression coverage proving withdrawal prevents assessment queueing and exposes the exact message.

- [ ] **Step 1: Write the failing test**

Add a test that creates a completed session and recording, grants consent, withdraws consent, then calls `AssessmentService.queue_assessment`. Assert `AssessmentStateError` contains `Recording consent was withdrawn; this session cannot be graded.` and assert no `AssessmentJob` exists.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_assessment_contracts.py -k withdrawn -vv`

Expected: FAIL because queueing currently accepts a completed recording after withdrawal.

### Task 2: Implement session non-gradability and API response contract

**Files:**
- Modify: `src/classsimvr/models/core.py` or the existing session model
- Modify: `src/classsimvr/services/recordings.py`
- Modify: `src/classsimvr/services/assessment.py`
- Modify: `src/classsimvr/services/assessment_contracts.py`
- Modify: `src/classsimvr/routes/assessment.py`
- Modify: `tests/test_openapi_contract.py`

**Interfaces:**
- Consumes: Task 1's failing test and existing consent withdrawal flow.
- Produces: `Session.gradability_status`/equivalent persisted state, `AssessmentStateError` with stable `code`, and HTTP 409 response fields `{code, status, message}`.

- [ ] **Step 1: Mark the session non-gradable during withdrawal**

Use the existing session row and set a durable status/reason when `withdraw_consent` succeeds. Keep the lesson lifecycle status independent so a running lesson can continue.

- [ ] **Step 2: Reject assessment queueing**

Before the completed-recording check can queue a job, reject a non-gradable session with code `recording_consent_withdrawn` and message `Recording consent was withdrawn; this session cannot be graded.`

- [ ] **Step 3: Add the structured assessment error response**

Map the domain error to HTTP 409 with a response body containing `code`, `status: "not_gradable"`, and `message`. Keep ordinary missing-session and invalid-state errors compatible with the existing API.

- [ ] **Step 4: Update the OpenAPI contract test**

Assert the assessment route documents or returns the structured non-gradable response fields used by Unreal.

- [ ] **Step 5: Run focused tests to verify green**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_assessment_contracts.py tests/test_recording_routes.py tests/test_openapi_contract.py -q`

Expected: all focused tests pass.

### Task 3: Document Unreal behavior and verify the project

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the stable `recording_consent_withdrawn` / `not_gradable` response.
- Produces: client integration instructions that tell Unreal to show the message and continue the lesson without grade UI.

- [ ] **Step 1: Document client behavior**

Explain that Unreal should keep the lesson active, disable recording controls, show the returned message, hide/disable grading submission, and preserve the status for the session summary.

- [ ] **Step 2: Run the full test suite**

Run: `\.venv\Scripts\python.exe -m pytest -q --tb=short`

Expected: all tests pass; if the existing Windows `TestClient` hang recurs, report the exact test and do not claim a full pass.

- [ ] **Step 3: Run hygiene checks**

Run source/documentation checks, `git diff --check` where applicable, and confirm no disposable `__pycache__` or `.pytest_cache` directories remain in the project.
