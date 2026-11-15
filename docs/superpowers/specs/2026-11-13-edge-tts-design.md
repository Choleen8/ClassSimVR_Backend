# Edge TTS integration design

## Goal

Generate local MP3 audio for spoken learner replies while preserving the existing
behavior response contract and both legacy and versioned behavior routes.

## Decisions

- Use `edge-tts` 7.3.1 through its async `Communicate` API, wrapped by the
  existing synchronous service interface.
- Select voices by stable persona ID: `maria-sandoval` uses
  `fil-PH-BlessicaNeural`; `robert-eco` uses `fil-PH-AngeloNeural`.
- Store generated files in the existing backend `audio/` directory with a
  sanitized stem plus UUID, and return the existing `audio_url` field.
- Keep `audio_url` null when `speak` is false, dialogue is empty, or TTS fails.
- Keep dialogue/subtitle data and animation fields intact on TTS failure.
- Keep the public base URL configurable through `PUBLIC_BASE_URL`.

## Out of scope

No Unreal project changes, microphone/STT work, or audio playback nodes are part
of this backend change.
