# ClassSim VR Learner-Behavior Service Design

## Intent

Add the first usable AI service for ClassSim VR: a token-bounded learner-behavior service that accepts a trainee utterance plus compact classroom context and returns one structured learner response. The service will use the OpenAI Responses API through the existing Python backend and will remain independent from the future recording-based assessment engine.

## Goals

- Keep OpenAI access behind one injectable client boundary.
- Build prompts from compact persona, scene, summary, and recent-turn context.
- Enforce input/output token budgets before each request.
- Return validated structured data: learner, dialogue, emotion, and action.
- Provide bounded retries and a deterministic local fallback when the API is unavailable.
- Expose a minimal FastAPI endpoint without storing secrets or raw prompts in logs.
- Make automated tests run without network calls or a live database.

## Non-goals

- Implementing the recording-based assessment engine.
- Implementing speech-to-text, text-to-speech, VR/WebSocket integration, or Unreal code.
- Creating database migrations or persisting turns in this first service slice.
- Sending the complete session transcript on every request.

## Token-management decisions

- Default behavior budget: at most 1,200 estimated input tokens and 180 output tokens.
- Include only a bounded recent-turn window plus a bounded session summary.
- Trim oversized persona and scene fields before constructing the request.
- Use one OpenAI request per behavior turn, with a small retry limit for transient failures.
- Return token usage metadata when the SDK provides it, without logging prompt contents.
- Use a local fallback response after retries fail.

## Service boundary

`BehaviorService.generate_reply(request: BehaviorRequest) -> BehaviorResponse` is the application boundary. The service accepts an injected OpenAI-compatible client so tests can use a fake response and production can construct the client from `OPENAI_API_KEY` only when needed.

## API boundary

The first HTTP endpoint is `POST /sessions/{session_id}/turn`. It validates the request, delegates to `BehaviorService`, and returns the structured behavior response. Session persistence and authentication remain later concerns.
