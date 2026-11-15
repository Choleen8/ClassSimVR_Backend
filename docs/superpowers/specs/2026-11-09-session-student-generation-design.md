# ClassSim VR Session-Only Student Generation Design

## Intent

Automatically create and assign a different simulated student roster whenever a trainee-teacher starts a practice session. The roster size must be configurable from 15 through 30 students, reflecting the updated project scope, while keeping generated students isolated to the current session.

## Paper alignment

The proposal describes a visually full-size Philippine class with a configurable number of active persona-driven foreground learners and an ambient crowd for performance. The updated implementation target is 15–30 students per class rather than the previous 30–50 range.

The feature supports:

- AI-driven virtual learner characters with distinct persona configurations.
- Philippine classroom language profiles: English, Filipino, and code-switched Taglish.
- Behavior states such as engaged, confused, disengaged, and disruptive.
- Scalable class density without requiring one OpenAI call per visible learner.
- Repeatable practice sessions with different learner compositions.
- Offline-tolerant operation because roster generation is local and deterministic.

## User behavior

When a session requests class generation:

1. The API validates that the requested class size is an integer from 15 to 30.
2. The service generates exactly that many distinct session-only students locally.
3. Each generated student receives a persona profile and initial runtime state.
4. Each generated student is automatically assigned to the session.
5. A new session receives a different roster when a different seed is used or when the server generates a new seed.
6. The response returns the generated roster and assignment state for the Unreal client.

The first implementation should not call OpenAI to generate the roster. A deterministic local generator built from bounded trait, language, baseline, and trigger catalogs avoids token cost, network latency, and unsafe/unreliable roster generation.

## Recommended API

```text
POST /api/v1/sessions/{session_id}/students/generate
```

Request:

```json
{
  "class_size": 20,
  "seed": 12345
}
```

`seed` is optional. If omitted, the service creates a random seed and returns it so the run can be reproduced for debugging or research logs.

Response shape:

```json
{
  "session_id": 42,
  "class_size": 20,
  "seed": 12345,
  "students": [
    {
      "session_id": 42,
      "persona_id": 101,
      "name": "Learner 01",
      "language": "Taglish",
      "current_state": "engaged",
      "current_emotion": "neutral",
      "is_active": true
    }
  ]
}
```

The response should use the existing explicit JSON DTO style and remain independent of SQLAlchemy objects so Unreal can consume it through its HTTP/JSON modules.

## Session-only persistence and cleanup

The generated records may be persisted during the session for state, turn logging, and recovery, but they must not behave as reusable global personas.

Recommended model addition:

- Add a nullable `generated_for_session_id` reference/ownership field to `PERSONA`.
- Generated personas set this field to the current session.
- Manually authored reusable personas leave it null.
- The API's global persona catalog excludes generated session-only personas by default.
- Session persona listing includes generated students for the current session.
- Session completion/reset cleanup removes generated personas owned by that session after dependent `SESSION_PERSONA` and `TURN` records are handled according to retention policy.

If the research workflow requires retaining completed session evidence, the generated persona profile may remain as a session-owned historical snapshot, but it must still be excluded from future class generation and global reusable-persona listings. The cleanup policy must not delete evidence required by a recording/report retention rule.

## Generation algorithm

Use a local seeded random generator and bounded catalogs:

- Names or learner labels.
- Language profiles: English, Filipino, Taglish.
- Academic baselines.
- Personality traits.
- Behavioral triggers.
- Response styles.
- Participation levels.
- Initial states and emotions.

The generator must guarantee:

- Exactly 15–30 students are produced.
- No duplicate persona assignment within a session.
- Bounded profile fields and JSON-safe state data.
- At least a configurable foreground subset marked active for AI behavior.
- Remaining students can be ambient/local in Unreal to control latency and token usage.
- Same seed plus same catalog version gives the same roster for reproducible tests.

The generator should not promise that every generated student is semantically unique. It should guarantee unique session identity and a varied bounded combination of attributes.

## Duplicate generation policy

Generating a class twice for the same session should not silently append another class. The first implementation should return `409 Conflict` when active/generated students already exist for the session. A separate reset endpoint or explicit `replace=true` decision can be added later after recording and retention semantics are defined.

## Alternatives considered

### Reusable global persona pool

Rejected for this request because the trainee should encounter a different roster each session, and the user explicitly wants the generated students to belong only to the current session.

### OpenAI-generated roster

Rejected for the first implementation because it adds token cost, latency, network dependency, nondeterminism, and validation burden to a feature that can be generated safely from local catalogs.

### Session-only in-memory roster

Rejected because runtime state, turn logs, recovery, and research reproducibility need a persisted session association while the session is active.

## Non-goals

- Unreal Engine code or visual avatar spawning in this backend change.
- Speech-to-text, text-to-speech, lip-sync, or facial animation.
- Automatic scoring or assessment generation.
- Allowing generated students to become global reusable personas automatically.
- Removing the existing manual persona assignment API.

## Acceptance criteria

- A valid request for any class size from 15 to 30 generates exactly that many students.
- Requests for 14 or 31 are rejected with a clear validation error.
- Generated students are automatically assigned to the requested session.
- A second generation request for the same session is rejected unless an explicit reset policy exists.
- Generated students from session A do not appear in session B's generated roster.
- Global reusable persona listing excludes session-only generated records.
- Generation performs no OpenAI calls and does not consume behavior-service tokens.
- Existing persona/session behavior routes remain compatible.
- Focused and complete tests pass.
