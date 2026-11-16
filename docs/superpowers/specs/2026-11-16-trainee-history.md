# Trainee Session History

## Goal

Expose a read-only history for one trainee so the client can show previous teaching sessions and their available assessment summaries.

## Business rules

- History is isolated by `trainee_id`.
- Results are ordered newest first by `created_at`, with `session_id` as a deterministic tie-breaker.
- An existing trainee with no sessions receives an empty list.
- A trainee ID that does not exist returns HTTP 404.
- A session may be listed before grading is available; report fields are then `null` and `report_available` is `false`.
- The endpoint does not invent an overall grade. Detailed indicator scores remain available through the existing session report endpoint.

## API contract

`GET /api/v1/trainees/{trainee_id}/history`

Each item contains the session configuration and lifecycle state, assessment availability, and the report summary when one exists:

- `session_id`, `trainee_id`
- `grade`, `competency`, `class_size`, `difficulty`
- `status`, `assessment_status`, `assessment_message`
- `created_at`, `started_at`, `completed_at`
- `report_available`, `summary`, `next_focus`

