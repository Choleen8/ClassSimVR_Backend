# ClassSim VR

ClassSim VR provides a PostgreSQL-backed model layer and a token-bounded learner-behavior service for the VR teaching simulation.

## Run the behavior API

Set `OPENAI_API_KEY` in `.env`, then start the local API from the project root:

```powershell
uvicorn classsimvr.app:app --reload
```

The first service endpoint is:

```text
POST /sessions/{session_id}/turn
```

The behavior service sends bounded persona, scene, summary, and recent-turn context to OpenAI. It limits output length, retries transient failures a fixed number of times, and returns a local fallback response when the provider is unavailable. Automated tests use an injected fake client and do not make network calls.

## Unreal integration contract

### Microphone transcription on a Windows NVIDIA laptop

Install the optional runtime in the backend environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu-windows.txt
```

For the tested RTX 2050 configuration, set these values in the local `.env` and restart the backend:

```dotenv
WHISPER_DEVICE=auto
WHISPER_COMPUTE_TYPE=int8_float16
WHISPER_MODEL=small
WHISPER_LANGUAGE=auto
```

Microphone start loads the model once and checks CUDA with an inference before recording. Automatic mode uses CPU int8 if CUDA is unavailable or runs out of memory. To reserve the GPU for VR, set `WHISPER_DEVICE=cpu` and `WHISPER_COMPUTE_TYPE=int8`. Check `GET /microphone/status` after starting to see the effective device and any fallback reason.

The `Response latency` and `Response drained` log entries share a command ID. The first measures time from the last speech frame to a queued response, including the configured pause. The second measures queue wait until the client drains the response. Audio playback on the headset happens after that point. Common acknowledgment MP3s are reusable across backend restarts, keyed by voice and spoken text.

Use the versioned JSON API from Unreal:

```text
GET  /api/v1/health
GET  /api/v1/auth/google/login
GET  /api/v1/auth/google/callback
GET  /api/v1/auth/me
POST /api/v1/auth/logout
POST /api/v1/sessions/{session_id}/students/generate
POST /api/v1/sessions
POST /api/v1/sessions/{session_id}/start
POST /api/v1/sessions/{session_id}/pause
POST /api/v1/sessions/{session_id}/resume
POST /api/v1/sessions/{session_id}/complete
POST /api/v1/sessions/{session_id}/reset
POST /api/v1/personas
GET  /api/v1/personas
GET  /api/v1/trainees/{trainee_id}/personas
POST /api/v1/sessions/{session_id}/personas
GET  /api/v1/sessions/{session_id}/personas
PATCH /api/v1/sessions/{session_id}/personas/{persona_id}
POST /api/v1/sessions/{session_id}/personas/{persona_id}/turn
POST /api/v1/sessions/{session_id}/turns
PUT  /api/v1/sessions/{session_id}/consent
DELETE /api/v1/sessions/{session_id}/consent
POST /api/v1/sessions/{session_id}/recording/start
POST /api/v1/sessions/{session_id}/recording/complete
POST /api/v1/sessions/{session_id}/recording/upload
GET  /api/v1/assessment/rubric
POST /api/v1/sessions/{session_id}/assessment
GET  /api/v1/assessment/jobs/{job_id}
POST /api/v1/assessment/jobs/{job_id}/result
GET  /api/v1/sessions/{session_id}/report
```

The versioned turn request contains only client context:

```json
{
  "trainee_input": "What is one half?",
  "scene_state": "teacher is demonstrating fractions",
  "recent_turns": []
}
```

## Google OAuth login

The backend implements the server-side Google OAuth authorization-code flow.
Configure the following values in the backend `.env` file:

```text
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/v1/auth/google/callback
APP_SESSION_SECRET=replace-with-a-long-random-secret
APP_SESSION_MAX_AGE_SECONDS=28800
APP_COOKIE_SECURE=false
```

Use these routes from the login client:

```text
GET  /api/v1/auth/google/login
GET  /api/v1/auth/google/callback
GET  /api/v1/auth/me
POST /api/v1/auth/logout
```

The callback verifies the Google identity, creates or reuses a trainee by
verified email, and sets a signed HttpOnly application-session cookie. The
Google client secret never goes to Unreal or the browser. Configure the exact
redirect URI in Google Cloud; production must use HTTPS and set
`APP_COOKIE_SECURE=true`.

The server resolves the assigned persona and current runtime state, then returns `persona_id`, `dialogue`, `emotion`, `action`, `fallback`, and optional token usage. This keeps Unreal independent from PostgreSQL and prevents client state from overwriting the persona profile.

Use the trainee persona list endpoint to populate a student's available personas across all of that student's sessions. It returns distinct persona profiles; use the session persona endpoint when the current runtime state for a specific session is needed.

The endpoints use stable `/api/v1` paths, integer IDs, bounded JSON fields, and ordinary HTTP. Keep Unreal calls behind a small API adapter so a future WebSocket or voice transport can be added without changing gameplay code.

## WebSocket transport

The HTTP API remains available and unchanged. For real-time learner turns, Unreal may connect to `ws://127.0.0.1:8000/api/v1/ws/sessions/{session_id}`. The server sends a `connected` event first. Send `{"type":"ping"}` for a `pong`, or send a bounded `turn` message containing `persona_id` and `trainee_input`. Responses use `turn_response` with the existing behavior and optional TTS fields. Invalid messages and unassigned personas return an `error` event while keeping the connection open. Use the HTTP endpoints as the fallback after reconnect.

## Voice listening and VAD configuration

The toggle-listen microphone service uses Silero VAD when `silero-vad` and its PyTorch runtime are installed. It combines Silero speech probability with an adaptive RMS noise floor. If Silero is unavailable or inference fails, it automatically falls back to adaptive RMS detection; the microphone API remains unchanged.

Recommended starting configuration for Quest Link:

```text
MICROPHONE_VAD_SILENCE_MS=1500
MICROPHONE_VAD_PROVIDER=silero
MICROPHONE_VAD_THRESHOLD=0.015
MICROPHONE_VAD_CALIBRATION_MS=1000
MICROPHONE_VAD_START_MULTIPLIER=2.5
MICROPHONE_VAD_HOLD_MULTIPLIER=1.5
MICROPHONE_VAD_SILERO_THRESHOLD=0.5
```

Use `MICROPHONE_VAD_SILENCE_MS=3000` when the teacher needs longer pauses. `/microphone/status` reports the active provider, calibrated noise floor, thresholds, speech probability, VAD timing, Whisper timing, routing timing, LLM timing, TTS timing, queue timing, and total response timing. A single Quest microphone cannot perfectly separate the teacher from nearby speakers; Silero plus adaptive gating reduces ordinary interference, while headset positioning, lower room volume, and noise suppression in Windows/Quest Link remain important.

## Privacy and compliance

The project includes a Philippine privacy/compliance baseline and a privacy-notice template in `docs/compliance/`. Consent now records its purpose and notice version, supports withdrawal, and prevents new recording after withdrawal. These are technical safeguards only; production use still requires the institution's DPO, privacy impact assessment, ethics approval, retention/deletion process, vendor review, security controls, and legal sign-off.

If recording consent is withdrawn during a lesson, the lesson may continue, but the session is marked `not_gradable`. Assessment requests return HTTP 409 with `code: recording_consent_withdrawn` and the message `Recording consent was withdrawn; this session cannot be graded.` Unreal should show that message, disable grading UI, and keep the lesson active.
