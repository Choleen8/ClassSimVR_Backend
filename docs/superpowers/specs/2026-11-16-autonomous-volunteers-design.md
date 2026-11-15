# Autonomous Classroom Volunteers

## Decision

Classroom invitations are handled as one batch decision. The backend sends the latest
classroom question and every registered student's participation profile in one structured
LLM request. The response is validated against the registry and may contain zero, one, or
many `RAISE_HAND` decisions. Students do not speak or change posture when volunteering.

## Fallback

If the provider is unavailable or returns invalid IDs/actions, a deterministic local score
uses knowledge level, confidence, willingness, personality text, and a rotating round factor.
This avoids random selection and avoids making the same students volunteer every round.

## Routing

The latest question is stored per classroom session. A later English, Filipino, or Taglish
invitation uses that question. If no question is available, the invitation returns an empty
classroom response rather than inventing context. Individual addressing remains strict and
does not use the last addressed student as an implicit target.
