from classsimvr.services.behavior import classify_teacher_input, infer_response_language


def test_physical_commands_do_not_request_speech():
    cases = {
        "Please stand up": "STAND",
        "Stand up": "STAND",
        "Please sit down": "SIT",
        "Raise your hand": "NONE",
        "Put your hand down": "LOWER_HAND",
    }

    for text, action in cases.items():
        intent = classify_teacher_input(text)
        assert intent.action == action
        assert intent.speak is False


def test_questions_without_punctuation_request_speech():
    for text in ("What is int in programming", "Explain what a variable is"):
        intent = classify_teacher_input(text)
        assert intent.speak is True


def test_physical_command_with_question_requests_both():
    for text in (
        "Stand up, what is int in programming",
        "Please stand up and tell me what Python is",
        "Stand up is the earth flat",
    ):
        intent = classify_teacher_input(text)
        assert intent.action == "STAND"
        assert intent.speak is True


def test_natural_english_tagalog_and_taglish_posture_requests_stay_silent():
    cases = (
        "Maria, please stand.",
        "Stand up, Maria.",
        "Please stand up, Maria.",
        "Maria, tumayo ka.",
        "Tumayo ka, Maria.",
        "Pwede bang tumayo ka, Maria?",
        "Robert, please stand up.",
        "Sit down, Robert.",
        "Robert, umupo ka na.",
    )

    for text in cases:
        intent = classify_teacher_input(text)
        assert intent.action in {"STAND", "SIT"}, text
        assert intent.speak is False, text


def test_tagalog_hand_commands_are_recognized_as_physical_only():
    cases = {
        "Maria, itaas mo ang kamay mo": "NONE",
        "Itaas ang kamay, Maria": "NONE",
        "Maria, taas kamay": "NONE",
        "Ibagsak ang kamay mo, Maria": "LOWER_HAND",
        "Maria, ibaba ang kamay": "LOWER_HAND",
    }

    for text, action in cases.items():
        intent = classify_teacher_input(text)
        assert intent.action == action, text
        assert intent.speak is False, text


def test_conversational_introductions_request_speech():
    for text in (
        "Maria Sandoval, introduce yourself",
        "Maria, what is a computer?",
        "Maria, magpakilala ka",
    ):
        intent = classify_teacher_input(text)
        assert intent.action == "NONE"
        assert intent.speak is True, text


def test_natural_multilingual_posture_variants_are_recognized():
    cases = {
        "Maria, maupo ka na": "SIT",
        "Maria, mag-stand ka": "STAND",
        "Robert, pwede ka nang umupo": "SIT",
        "Please get up, Maria": "STAND",
        "Robert, take a seat": "SIT",
    }
    for text, action in cases.items():
        intent = classify_teacher_input(text)
        assert intent.action == action, text
        assert intent.speak is False, text


def test_conflicting_or_negated_physical_commands_are_not_executed():
    for text in (
        "Maria, don't stand up",
        "Maria, stand up and sit down",
        "Maria, maybe stand up",
        "Maria, baka tumayo ka",
    ):
        intent = classify_teacher_input(text)
        assert intent.action == "NONE", text


def test_response_language_follows_each_utterance_and_supports_taglish():
    assert infer_response_language("Maria, please stand up", "English") == "English"
    assert infer_response_language("Maria, tumayo ka", "English") == "Filipino"
    assert infer_response_language("Maria, can you explain kung ano ito", "English") == "Taglish"
