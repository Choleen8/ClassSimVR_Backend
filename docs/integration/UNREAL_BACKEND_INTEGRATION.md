# Unreal Engine to ClassSim VR Backend Integration

This document defines the shared boundary between the Unreal VR client and the Python backend. Unreal should use an API adapter rather than calling database concepts directly.

## Responsibility split

| Capability | Backend | Unreal/shared client |
|---|---|---|
| Session and class roster | Owns session lifecycle and generates 15–30 session-only students | Requests roster and spawns corresponding visual learners |
| Persona behavior | Resolves persona/state and calls OpenAI with bounded context | Sends trainee/scene events and renders returned dialogue/emotion/action |
| Speech-to-text | Captures the Quest Link microphone, detects utterances with VAD, and transcribes locally | Toggles listening and polls routed responses |
| Text-to-speech | Chooses dialogue and language | Speaks dialogue and drives audio/lip-sync |
| Classroom visuals | No VR rendering | Owns OpenXR, MetaHuman, movement, gaze, gestures, animation, and ambient crowd |
| Recording | Requires consent and stores recording metadata/storage reference | Captures audio/video and uploads to configured storage or upload service |
| Assessment | Queues/polls assessment, validates rubric output, stores reports/evidence | Displays report and evidence; does not treat it as an official evaluation |
| Privacy | Consent/status/retention metadata and future authorization boundary | Shows consent UI and prevents recording before consent is granted |

## Local development

Start the backend from the project root:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m uvicorn classsimvr.app:app --host 0.0.0.0 --port 8000
```

Default local base URL:

```text
http://127.0.0.1:8000
```

The Unreal adapter should make the base URL configurable. Do not hard-code the local address in gameplay code.

## Session lifecycle

### 0. Create a scenario session

```http
POST /api/v1/sessions
Content-Type: application/json
```

```json
{
  "trainee_id": 7,
  "grade": "Grade 6",
  "competency": "Explain equivalent fractions",
  "lesson_plan": "Use visual fraction strips.",
  "answer_key": "Equivalent fractions have the same value.",
  "class_size": 20,
  "difficulty": "moderate"
}
```

`class_size`, when supplied, must be 15–30. This stores scenario configuration; call the roster endpoint next to generate the session-only students.

### 1. Generate the session roster

```http
POST /api/v1/sessions/{session_id}/students/generate
Content-Type: application/json
```

```json
{
  "class_size": 20,
  "seed": 12345
}
```

`class_size` must be 15–30. `seed` is optional. The backend generates a local deterministic roster and automatically assigns each student to the session. This does not call OpenAI.

The response contains `session_id`, `class_size`, `seed`, and `students`. Each student includes `persona_id`, `name`, language profile, stable traits/triggers, current state, emotion, and session assignment.

The roster is session-only. It should not be treated as a reusable persona catalog. A second generation request for the same active roster returns `409`.

### 2. Control the session

```text
POST /api/v1/sessions/{session_id}/start
POST /api/v1/sessions/{session_id}/pause
POST /api/v1/sessions/{session_id}/resume
POST /api/v1/sessions/{session_id}/complete
POST /api/v1/sessions/{session_id}/reset
```

Valid lifecycle states are `created`, `running`, `paused`, and `completed`. Invalid transitions return `409`. Reset returns the session to `created` and removes session-only generated students when no generated-student turns have been recorded. If evidence exists, reset is rejected to protect the record.

### 3. Load and render the roster

```http
GET /api/v1/sessions/{session_id}/personas
```

Use `persona_id` as the stable identity when spawning Unreal actors. The backend does not know the Unreal actor instance ID; the client should maintain a local mapping such as:

```text
persona_id -> Unreal actor/component reference
```

The backend currently returns generated students as active assignments. The Unreal client may render a full class while choosing a smaller foreground subset for live behavior calls; ambient learners should use local/scripted behavior.

## Live learner behavior

## Backend toggle-to-listen microphone

### Dynamic student registry

The microphone router is registry-backed. Maria and Robert Eco are default
registrations, but they are no longer the routing limit. Register additional
students with unique IDs, aliases, personality text, language, voice, and an
initial state:

```http
POST /students/register
Content-Type: application/json
```

```json
{
  "student_id": "ana-cruz",
  "name": "Ana Cruz",
  "aliases": ["ana", "miss cruz"],
  "personality": "Ana is analytical, shy, and asks for examples.",
  "language": "Taglish",
  "voice": "fil-PH-BlessicaNeural",
  "initial_state": "engaged"
}
```

List registered students with `GET /students`. The returned `student_id` is
the stable identity Unreal should map to its actor. Aliases are used for
spoken targeting, so “Ana, please stand up” resolves to `ana-cruz`.

Each student has independent state per session:

```text
GET   /students/{student_id}/sessions/{session_id}/state
PATCH /students/{student_id}/sessions/{session_id}/state
```

The registry can also load a JSON array from the
`CLASSSIMVR_STUDENTS_JSON` environment variable at startup. Registration
fails with `409` when an ID or alias is already in use. Classroom-wide
commands return one response per registered student, while direct posture
commands are handled locally and do not require provider calls for every
student.

The backend can capture the Windows Quest Link microphone continuously while
listening is enabled and transcribe completed utterances locally. The default
device is:

```text
Headset Microphone (Oculus Virtual Audio Device)
```

`GET /microphone/status` reports the selected `host_api`, `sample_rate`, and
`channels`. The Oculus device is selected by exact name with Windows WASAPI
preferred when Windows exposes the same name through multiple PortAudio host
APIs. The tested Quest Link configuration uses 48,000 Hz, mono input.
WASAPI is opened in shared mode, and initialization retries supported sample
rates and fallback input host APIs while closing every failed stream.

Unreal should call these endpoints for toggle listening:

```text
POST /microphone/start
POST /microphone/stop
GET  /microphone/status
GET  /microphone/responses
POST /microphone/pause
POST /microphone/resume
POST /microphone/cancel
```

Press X once to enable listening:

```json
{
  "session_id": "classroom",
  "scene_state": "The teacher is discussing photosynthesis."
}
```

The backend VAD detects a pause, transcribes the completed utterance, routes
it automatically, and places the normal learner response in a bounded queue.
Poll it while listening remains enabled:

```http
GET /microphone/responses?session_id=classroom&limit=10&drain=true
```

The response contains one or more normal learner responses, so Unreal can
reuse the same action, subtitle, talking-animation, and audio playback
handling already used by `/sessions/{session_id}/turn`:

```json
{
  "transcript": "Maria, please stand up.",
  "target": "maria-sandoval",
  "responses": [
    {
      "persona_id": "maria-sandoval",
      "dialogue": "",
      "emotion": "neutral",
      "action": "STAND",
      "speak": false,
      "audio_url": null,
      "fallback": false,
      "usage": null
    }
  ]
}
```

For a spoken answer, `speak` is true and `audio_url` points to the existing
HTTP-served MP3. The VAD keeps only the current utterance, limits utterance
length, and bounds the pending queue, so a long classroom session does not
accumulate unlimited audio. The backend never permanently stores microphone
audio.

### Unreal Blueprint connection order

1. Add the left-controller X action to the existing Enhanced Input mapping.
2. On X `Started`, branch on a local `IsListening` boolean.
3. If false, call `POST /microphone/start` once and set `IsListening=true`.
4. If true, call `POST /microphone/stop` once and set `IsListening=false`.
5. While listening, poll `GET /microphone/responses` every 100–250 ms.
6. For each queued item, loop through `responses` and route each response by
   `persona_id` to the matching student.
7. Apply `action` to the existing stand/sit/raise/lower/talk functions.
8. Before playing a student `audio_url`, call `POST /microphone/pause`.
9. Display `dialogue` as the subtitle and play the MP3/talking animation.
10. When audio playback finishes, call `POST /microphone/resume`.
11. If the request fails or the user exits VR, call `POST /microphone/cancel`.

Do not add Unreal Audio Capture for this flow; that was the component that
caused the VR Preview shutdown crash.

```http
POST /api/v1/sessions/{session_id}/personas/{persona_id}/turn
Content-Type: application/json
```

```json
{
  "trainee_input": "Why did you choose that answer?",
  "scene_state": "The teacher is reviewing fractions near the board.",
  "recent_turns": [
    {"speaker": "learner", "text": "I counted the shaded parts."}
  ]
}
```

The backend resolves the persona and session runtime state from the IDs. Do not send persona profile fields from Unreal for this versioned route.

## WebSocket real-time transport

The HTTP API remains unchanged. Unreal may use the additive WebSocket endpoint for lower-overhead turn delivery:

```text
ws://{host}:{port}/api/v1/ws/sessions/{session_id}
```

The server sends `{"type":"connected","session_id":42}` after connection. Send `{"type":"ping"}` to receive `{"type":"pong"}`. Turn requests use:

```json
{
  "type": "turn",
  "persona_id": 7,
  "trainee_input": "What is one half?",
  "scene_state": "The class is reviewing fractions.",
  "recent_turns": []
}
```

Turn responses use `{"type":"turn_response","data":{...}}`, where `data` has the same behavior and optional TTS fields as the HTTP response. Invalid messages or unassigned personas return an `error` event while keeping the connection open. After a disconnect, reconnect and refresh session state over HTTP; HTTP remains the compatibility fallback.

Response:

```json
{
  "persona_id": "12",
  "dialogue": "I counted the shaded parts.",
  "emotion": "confident",
  "action": "points_to_board",
  "fallback": false,
  "usage": null
}
```

`fallback=true` means the provider was unavailable or returned unusable output. Unreal should still render the dialogue and may show a subtle degraded-network indicator. The backend bounds context, output length, and retry count; never send an unbounded transcript.

After receiving a response, persist the event:

```http
POST /api/v1/sessions/{session_id}/turns
```

```json
{
  "persona_id": 12,
  "teacher_text": "Why did you choose that answer?",
  "learner_text": "I counted the shaded parts.",
  "emotion": "confident",
  "action": "points_to_board",
  "source": "behavior_service",
  "t_offset_s": 44.2
}
```

The turn endpoint rejects personas not assigned to the session.

## Consent and recording handoff

Consent must be recorded before capture:

```http
PUT /api/v1/sessions/{session_id}/consent
Content-Type: application/json
```

```json
{"video_recording": true}
```

Start metadata after the trainee accepts:

```http
POST /api/v1/sessions/{session_id}/recording/start
```

```json
{
  "mime_type": "video/webm",
  "storage_ref": "upload-token-or-object-reference"
}
```

Complete after Unreal or a storage adapter finishes the upload:

```http
POST /api/v1/sessions/{session_id}/recording/complete
```

```json
{
  "duration_s": 125.5,
  "size_bytes": 8123456,
  "storage_ref": "object://recordings/session-42.webm"
}
```

The backend stores metadata and a storage reference. It does not expose local filesystem paths or API secrets. Production should replace the reference with a signed object-storage workflow.

## Assessment queue and polling

After the session is completed and the recording is complete:

```http
POST /api/v1/sessions/{session_id}/assessment
```

The response is `202 Accepted` with an assessment job. Poll:

```http
GET /api/v1/assessment/jobs/{job_id}
```

Job states are `pending`, `running`, `completed`, or `failed`. The current backend implements the queue contract and validation boundary. A production multimodal provider worker still needs to process the audio-video object.

When a provider worker has validated its result, it submits:

```http
POST /api/v1/assessment/jobs/{job_id}/result
```

The backend validates scores from 1–4, observed/not-observed rules, BTI codes, and evidence timestamps, then persists the report. Read it with:

```http
GET /api/v1/sessions/{session_id}/report
```

The rubric is available at:

```http
GET /api/v1/assessment/rubric
```

It contains the ten PPST/BTI mappings defined in the proposal. Observed scores are limited to 1–4; evidence should contain recording timestamps and an explanation.

## Status and retry behavior

| Status | Meaning | Unreal behavior |
|---:|---|---|
| 200 | Read/update succeeded | Continue |
| 201 | Roster/recording/turn created | Store returned IDs |
| 202 | Assessment queued | Poll job status |
| 404 | Session/persona/job not found | Stop and surface a recoverable setup error |
| 409 | Invalid lifecycle, missing consent, duplicate generation, or invalid state | Do not blindly retry; fix client flow |
| 422 | Invalid JSON or bounds | Fix payload |
| 5xx | Backend/provider failure | Retry with bounded backoff; behavior route has a safe fallback |

Use idempotent client state and avoid firing duplicate generation, recording-start, or assessment requests during reconnects. A future version should add request idempotency keys for network retries.

## Security and privacy

- Keep `OPENAI_API_KEY` and database credentials on the backend only.
- Use HTTPS outside local development.
- Obtain and display consent before recording.
- Do not log raw audio/video, API keys, or full sensitive payloads.
- Add authentication and supervisor/institution authorization before deployment.
- Apply retention/deletion policy without deleting evidence still needed by an active assessment or approved research record.
