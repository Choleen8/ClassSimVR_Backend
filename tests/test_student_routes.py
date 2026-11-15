from classsimvr.routes.students import (
    StudentRegistrationBody,
    StudentStateBody,
    get_student_state,
    list_students,
    register_student,
    update_student_state,
)
from classsimvr.services.student_registry import StudentRegistry


def test_student_registration_exposes_voice_and_profile_configuration():
    registry = StudentRegistry()
    response = register_student(
        StudentRegistrationBody(
            student_id="ana-01",
            name="Ana Cruz",
            aliases=["ana", "miss cruz"],
            personality="Ana is analytical and shy.",
            voice="fil-PH-BlessicaNeural",
        ),
        registry,
    )

    assert response.student_id == "ana-01"
    assert response.voice == "fil-PH-BlessicaNeural"
    assert response.aliases == ["ana", "miss cruz"]
    assert len(list_students(registry)) == 1


def test_student_registration_exposes_individual_session_state():
    registry = StudentRegistry()
    register_student(
        StudentRegistrationBody(student_id="ana-01", name="Ana Cruz"),
        registry,
    )

    updated = update_student_state(
        "ana-01",
        "session-1",
        StudentStateBody(current_state="confused", emotion="curious", summary="needs an example"),
        registry,
    )
    loaded = get_student_state("ana-01", "session-1", registry)

    assert updated.current_state == "confused"
    assert loaded.summary == "needs an example"
