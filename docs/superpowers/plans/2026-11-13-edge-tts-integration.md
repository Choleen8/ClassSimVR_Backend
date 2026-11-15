# Edge TTS integration plan

1. Replace the ElevenLabs service with an Edge TTS implementation and persona
   voice mapping.
2. Attach generated audio URLs in both existing behavior endpoints, preserving
   the response schema and graceful fallback behavior.
3. Update dependency manifests and focused tests for voice selection, unique
   MP3 output, `speak=false`, and TTS failure fallback.
4. Install `edge-tts==7.3.1`, run focused and full tests, and perform one real
   synthesis plus static audio URL verification.
