from classsimvr.app import app


def test_unreal_backend_contract_routes_are_published():
    paths = set(app.openapi()["paths"])

    required = {
        "/api/v1/health",
        "/api/v1/sessions/{session_id}/students/generate",
        "/api/v1/sessions/{session_id}/start",
        "/api/v1/sessions/{session_id}/reset",
        "/api/v1/sessions/{session_id}/turns",
        "/api/v1/sessions/{session_id}/consent",
        "/api/v1/sessions/{session_id}/recording/start",
        "/api/v1/sessions/{session_id}/recording/complete",
        "/api/v1/sessions/{session_id}/assessment",
        "/api/v1/assessment/jobs/{job_id}",
        "/api/v1/assessment/rubric",
        "/microphone/start",
        "/microphone/stop",
        "/microphone/status",
        "/microphone/cancel",
        "/microphone/responses",
        "/microphone/pause",
        "/microphone/resume",
        "/students",
        "/students/register",
        "/students/{student_id}/sessions/{session_id}/state",
    }

    assert required <= paths
