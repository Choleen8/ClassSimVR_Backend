# Student Voice Command Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all registered student voice commands robust to name position and natural English, Tagalog, and Taglish phrasing.

**Architecture:** Keep the existing microphone and response APIs unchanged. Normalize transcript text in the registry and deterministic behavior classifier, resolve any registered alias anywhere in the sentence, and preserve the existing silent physical-command fast path.

**Tech Stack:** Python, FastAPI, pytest, Pydantic, existing `StudentRegistry` and `BehaviorService`.

**Spec:** `docs/superpowers/specs/2026-11-15-student-voice-command-normalization-design.md`

## Global Constraints

- Do not modify Unreal Engine files or Blueprints.
- Preserve existing microphone endpoints and JSON response format.
- Apply matching to all current and future registered students.
- Physical-only commands must return `speak=false` and `audio_url=null`.

## Review Focus

- Student name at sentence start/end/middle and punctuation: registry resolves the intended profile.
- Polite filler such as “please” and “pwede bang”: action remains deterministic.
- Tagalog/Taglish variants: `tumayo`, `umupo`, `taas/itaas ang kamay`, `ibaba ang kamay` map correctly.
- Alias collisions and partial names: only registered whole aliases match.
- Classroom-wide commands: “everyone/lahat/buong klase” still target all students.

### Task 1: Add failing registry and intent tests

**Files:**
- Modify: `tests/test_student_registry.py`
- Modify: `tests/test_microphone_routing.py`
- Create: `tests/test_behavior_intent.py`

**Interfaces:**
- Consumes: existing `StudentRegistry.resolve`, `classify_teacher_input`, and `MicrophoneCommandRouter.handle`.
- Produces: executable examples for all requested sentence structures and actions.

- [ ] Add tests for aliases at the beginning, middle, and end of sentences, including punctuation.
- [ ] Add tests for English, Tagalog, Taglish, polite stand/sit/hand commands and silent response fields.
- [ ] Add a future registered student test to prove no Maria/Robert hardcoding.
- [ ] Run the focused tests and confirm the new cases fail before implementation.

### Task 2: Normalize registry matching

**Files:**
- Modify: `src/classsimvr/services/student_registry.py`
- Test: `tests/test_student_registry.py`

**Interfaces:**
- Consumes: `StudentProfile.normalized_aliases()` and transcript strings.
- Produces: `StudentRegistry.resolve(text: str) -> StudentProfile | None` with normalized whole-alias matching.

- [ ] Add shared text normalization for case, punctuation, repeated whitespace, and common Unicode apostrophe variants.
- [ ] Resolve the longest registered alias anywhere in normalized text using token-safe matching.
- [ ] Preserve duplicate-alias protection and dynamic registration behavior.
- [ ] Run registry tests and confirm they pass.

### Task 3: Expand deterministic command intent normalization

**Files:**
- Modify: `src/classsimvr/services/behavior.py`
- Test: `tests/test_behavior_intent.py`

**Interfaces:**
- Consumes: normalized teacher transcript.
- Produces: existing `TeacherIntent(action: str, speak: bool)` from `classify_teacher_input`.

- [ ] Normalize punctuation and polite filler without removing command words.
- [ ] Add natural English, Filipino, and Taglish phrase families for existing supported actions.
- [ ] Keep question/answer detection separate so physical-only requests remain `speak=False`.
- [ ] Run behavior intent tests and confirm they pass.

### Task 4: Verify routing and compatibility

**Files:**
- Modify: `tests/test_microphone_routing.py`
- Modify: `tests/test_microphone_routes.py` only if a response compatibility assertion is needed.

**Interfaces:**
- Consumes: registry resolution and behavior intent from Tasks 2–3.
- Produces: unchanged `CommandResult` and `BehaviorResponse` JSON fields.

- [ ] Verify named commands route to the resolved dynamic student regardless of name position.
- [ ] Verify physical commands do not call TTS and return `audio_url=null`.
- [ ] Run the complete existing test suite and compile checks.
- [ ] Confirm no Unreal files changed.
