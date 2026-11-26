from __future__ import annotations

import json
import os
import re
import unicodedata
import time
import logging
import threading
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from .contracts import BehaviorRequest, BehaviorResponse, TokenUsage
from .academic_ability import AcademicAbilityController, SelectedBehavior
from .openai_client import create_openai_client
from .token_budget import TokenBudget
from .token_budget import estimate_tokens
from .student_registry import normalize_grade_level
from .session_contracts import ClassroomContext
from hashlib import sha256

logger = logging.getLogger(__name__)

BEHAVIOR_INSTRUCTIONS = """You are a virtual learner in a Philippine classroom simulation.
Respond only with a JSON object containing exactly these fields:
persona_id, dialogue, emotion, action, speak.
Stay in character, answer the trainee's latest input, and keep dialogue under 40 words.
Use the requested language when possible. Do not include markdown or extra commentary.
The action must be exactly one of: NONE, STAND, SIT, TALK, STOP_TALKING, RAISE_HAND, LOWER_HAND, THINKING, IDK.
Only use THINKING or IDK when that student's supported_actions includes the action. THINKING is a silent
preparation state before an attempted spoken answer; IDK is a respectful spoken admission of uncertainty.
Consider the question's difficulty together with knowledge_level, confidence, willingness, and personality.
If grade_level is provided, treat it as a curriculum boundary: use grade-appropriate vocabulary and
do not give advanced explanations beyond that level. For difficult but partly understood questions,
give a short partial answer and use THINKING when supported. For unfamiliar or clearly beyond-grade
questions, do not invent an answer; use THINKING followed by a brief, respectful IDK response when
those actions are supported.
If grade_level is UNSELECTED, do not assume College-level knowledge; stay conservative and acknowledge
uncertainty for advanced topics.
If academic_behavior is IDK, do not answer the academic question; give a brief respectful uncertainty
response. If it is THINKING, give a concise answer or partial answer suitable for that state.
Use STAND or SIT when the trainee gives a direct posture command.
Use NONE for a question-only answer; the separate speak field controls the talking animation. Use TALK only when the learner's explicit behavior requires the TALK action. Use RAISE_HAND only when the learner volunteers to answer.
Set speak to true whenever the student's dialogue should be spoken aloud with the talking animation, including answers, introductions, clarification requests, and answers after a posture command such as "please stand and explain...".
When the teacher directly addresses the student with a physical command, acknowledge it naturally in the same spoken response while performing the action.
Set speak to false only when no spoken reply is needed.
Never return an empty dialogue when the trainee asks a conversational question or requests an introduction; provide a brief natural response instead.
Use the chronological recent_turns and session_summary as the only source for recalling earlier questions or answers. Never invent a previous turn. If the requested detail is not present, politely say that you cannot remember.
Use NONE when no animation is needed."""

ACTION_VALUES = [
    "NONE",
    "STAND",
    "SIT",
    "TALK",
    "STOP_TALKING",
    "RAISE_HAND",
    "LOWER_HAND",
    "THINKING",
    "IDK",
]

BEHAVIOR_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "persona_id": {"type": "STRING"},
        "dialogue": {"type": "STRING"},
        "emotion": {"type": "STRING"},
        "action": {"type": "STRING", "enum": ACTION_VALUES},
        "speak": {"type": "BOOLEAN"},
    },
    "required": ["persona_id", "dialogue", "emotion", "action", "speak"],
}

CLASSROOM_SCOPE_INSTRUCTIONS = """
Use classroom_session for the active grade, subject, and mode. Classify the teacher's latest
request in subject_relation: RELATED for academic content supporting the lesson (including
cross-disciplinary examples), UNRELATED only for clearly off-subject academics, OTHER for
greetings, classroom directions, recall, clarification, or ambiguous requests.
For UNRELATED, give no lesson answer. Follow the teacher's English, Filipino, or Taglish
in every subject. The subject is academic scope, independent of the spoken language.
Keep the learner's own personality and depth appropriate to knowledge_level.
"""
SCOPED_BEHAVIOR_INSTRUCTIONS = BEHAVIOR_INSTRUCTIONS.replace(
    "persona_id, dialogue, emotion, action, speak.",
    "persona_id, dialogue, emotion, action, speak, subject_relation.",
) + CLASSROOM_SCOPE_INSTRUCTIONS
SUBJECT_RELATIONS = ("RELATED", "UNRELATED", "OTHER")
SCOPED_RESPONSE_SCHEMA = {
    **BEHAVIOR_RESPONSE_SCHEMA,
    "properties": {
        **BEHAVIOR_RESPONSE_SCHEMA["properties"],
        "subject_relation": {"type": "STRING", "enum": list(SUBJECT_RELATIONS)},
    },
    "required": [*BEHAVIOR_RESPONSE_SCHEMA["required"], "subject_relation"],
}

VOLUNTEER_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "persona_id": {"type": "STRING"},
                    "action": {"type": "STRING", "enum": ["RAISE_HAND", "NONE"]},
                },
                "required": ["persona_id", "action"],
            },
        }
    },
    "required": ["decisions"],
}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class TeacherIntent:
    action: str
    speak: bool


_COMMAND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LOWER_HAND", (
        "lower your hand", "lower hand", "put your hand down", "hands down",
        "ibaba ang kamay", "ibaba mo ang kamay", "ibaba ang iyong kamay",
        "ibagsak ang kamay", "ibagsak mo ang kamay", "ibaba ang kamay mo",
    )),
    ("STAND", (
        "stand up", "please stand", "get up", "stand", "tumayo", "tayo",
        "mag stand", "paki tayo", "paki tumayo", "makatayo",
    )),
    ("SIT", (
        "sit down", "please sit", "take a seat", "have a seat", "sit", "umupo",
        "upo", "maupo", "mag sit", "paki upo", "makaupo",
    )),
)

_QUESTION_PATTERNS = (
    r"\?",
    r"\bwhat\b",
    r"\bwhy\b",
    r"\bhow\b",
    r"\bwho\b",
    r"\bwhen\b",
    r"\bwhere\b",
    r"\bexplain\b",
    r"\bname\b",
    r"\blist\b",
    r"\bidentify\b",
    r"\benumerate\b",
    r"\btell me\b",
    r"\bdescribe\b",
    r"\bdefine\b",
    r"\bano\b",
    r"\bbakit\b",
    r"\bpaano\b",
    r"\bsino\b",
    r"\b(is|are|does|do|can|will|should)\b",
)

_ACADEMIC_IMPERATIVE_PATTERNS = (
    r"\bderive\b",
    r"\bcalculate\b",
    r"\bcompare\b",
    r"\bevaluate\b",
    r"\bprove\b",
    r"\banalyze\b",
    r"\bsolve\b",
)

_CONVERSATIONAL_PATTERNS = (
    r"\bintroduce yourself\b",
    r"\bintroduce your self\b",
    r"\btell me about yourself\b",
    r"\bwho are you\b",
    r"\bmagpakilala\b",
    r"\bpakilala ka\b",
    r"\b(?:hello|hi|good morning|good afternoon|kumusta|kamusta|magandang umaga|magandang hapon)\b",
)

_BEYOND_SCHOOL_TOPIC_PATTERNS = (
    r"\bquantum(?:\s+field)?\s+(?:physics|theory|mechanics)\b",
    r"\bquantum field theory\b",
    r"\bquantum entanglement\b",
    r"\bstring theory\b",
    r"\b(general|special) relativity\b",
    r"\bschrodinger(?:'s)? equation\b",
    r"\btensor calculus\b",
    r"\bdifferential equations?\b",
    r"\badvanced calculus\b",
    r"\borganic chemistry\b",
)


def _is_beyond_grade_level(trainee_input: str, grade_level: str | None) -> bool:
    """Conservatively flag topics that should not be answered as known by a K-12 learner."""
    normalized_grade = normalize_grade_level(grade_level)
    if normalized_grade in (None, "College"):
        return False
    return any(re.search(pattern, _normalize_teacher_text(trainee_input)) for pattern in _BEYOND_SCHOOL_TOPIC_PATTERNS)


def _grade_limited_reply(request: BehaviorRequest) -> BehaviorResponse:
    language = infer_response_language(request.trainee_input, request.language)
    if language == "Filipino":
        dialogue = "Pasensya na po, Ma'am. Hindi ko po alam ang sagot."
    elif language == "Taglish":
        dialogue = "Sorry po, Ma'am. Hindi ko po alam ang sagot."
    else:
        dialogue = "I'm sorry, Ma'am. I don't know the answer yet."
    return BehaviorResponse(
        persona_id=request.persona_id,
        dialogue=dialogue,
        emotion="uncertain",
        action="THINKING",
        speak=True,
        fallback=False,
    )


def classify_teacher_input(trainee_input: str) -> TeacherIntent:
    """Classify animation intent without relying on punctuation from speech-to-text."""
    text = _normalize_teacher_text(trainee_input)
    matched_actions = [
        candidate
        for candidate, phrases in _COMMAND_PATTERNS
        if any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in phrases)
    ]
    action = matched_actions[0] if len(set(matched_actions)) == 1 else "NONE"
    if action != "NONE" and re.search(
        r"\b(?:don't|don t|do not|dont|huwag|wag|maybe|perhaps|parang|siguro|baka)\b",
        text,
    ):
        action = "NONE"

    # A question mark alone does not mean the student should speak. In
    # natural classroom speech, "Pwede bang tumayo ka, Maria?" and
    # "Can you sit down, Robert?" are still physical-only requests. Keep
    # semantic question words so combined commands such as "stand up and
    # explain photosynthesis" continue to request a spoken answer.
    asks_for_answer = any(
        re.search(pattern, text)
        for pattern in _QUESTION_PATTERNS
        if pattern != r"\?" and pattern != r"\b(is|are|does|do|can|will|should)\b"
    ) or bool(re.search(r"\b(is|are|does|do)\b", text))
    asks_for_answer = asks_for_answer or any(
        re.search(pattern, text) for pattern in _ACADEMIC_IMPERATIVE_PATTERNS
    )
    asks_for_answer = asks_for_answer or any(
        re.search(pattern, text) for pattern in _CONVERSATIONAL_PATTERNS
    )
    return TeacherIntent(action=action, speak=asks_for_answer)


def _normalize_teacher_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", without_accents)).strip()


_TAGALOG_WORDS = {
    "ako", "ang", "ano", "bakit", "bang", "ka", "kayo", "kung", "mag", "magpakilala",
    "mo", "na", "nakakaintindi", "paki", "pakilala", "pwede", "puwede", "saan", "sino",
    "ito", "iyon", "tayo", "taas", "tumayo", "umupo", "upo", "iba", "hindi", "salamat",
}
_ENGLISH_WORDS = {
    "a", "about", "are", "can", "computer", "could", "down", "explain", "get", "hand", "how",
    "introduce", "is", "please", "sit", "stand", "tell", "the", "up", "what", "who", "why",
}


def infer_response_language(trainee_input: str, configured_language: str = "English") -> str:
    """Infer the teacher's language per utterance for multilingual replies."""
    tokens = set(_normalize_teacher_text(trainee_input).split())
    has_tagalog = bool(tokens & _TAGALOG_WORDS)
    has_english = bool(tokens & _ENGLISH_WORDS)
    if has_tagalog and has_english:
        return "Taglish"
    if has_tagalog:
        return "Filipino"
    return configured_language.strip() or "English"


class BehaviorService:
    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        budget: TokenBudget | None = None,
        max_retries: int | None = None,
        fallback_responses: dict[str, str] | None = None,
        academic_controller: AcademicAbilityController | None = None,
    ) -> None:
        load_dotenv()
        self.client = client
        self.model = model or os.getenv("OPENAI_BEHAVIOR_MODEL", "gpt-6-luna")
        self.budget = budget or TokenBudget.from_env()
        self.max_retries = (
            _bounded_env_int("OPENAI_BEHAVIOR_MAX_RETRIES", 1, 0, 1)
            if max_retries is None
            else max(0, max_retries)
        )
        self.fallback_responses = fallback_responses or {}
        self.academic_controller = academic_controller or AcademicAbilityController()
        self._timings = threading.local()

    @property
    def _last_latency_ms(self) -> dict[str, float | None]:
        if not hasattr(self._timings, "stages"):
            self._timings.stages = {"llm_generation": None, "classroom_volunteer_decision": None}
        return self._timings.stages

    def decide_classroom_volunteers(
        self,
        question: str,
        students: list[Any],
        *,
        round_number: int = 0,
        classroom_context: ClassroomContext | None = None,
    ) -> dict[str, str]:
        """Make one validated, independent volunteer decision for every student."""
        if not question.strip() or not students:
            return {}
        allowed = {student.student_id: student for student in students}
        payload = {
            "classroom_question": question.strip()[:4000],
            "round_number": round_number,
            "students": [
                {
                    "persona_id": student.student_id,
                    "name": student.name,
                    "personality": str(student.personality)[:240],
                    "knowledge_level": student.knowledge_level,
                    "confidence": student.confidence,
                    "willingness": student.willingness,
                }
                for student in students
            ],
        }
        instruction = (
            "You are evaluating classroom participation. For every listed student, make an "
            "independent decision based on the question, knowledge_level, personality, "
            "confidence, willingness, and round_number. Zero, one, or many may volunteer; "
            "do not choose randomly and do not make everyone volunteer. Return only JSON with "
            "decisions, one entry per listed persona_id. action must be exactly RAISE_HAND or "
            "NONE. Volunteers remain seated and silent; do not generate dialogue."
        )
        if classroom_context:
            payload["classroom_session"] = classroom_context.prompt_context()
            instruction += " Consider the active grade and subject; choose NONE for a clearly unrelated academic question."
        input_cap = _bounded_env_int("OPENAI_CLASSROOM_MAX_INPUT_TOKENS", 2_400, 1_024, 6_000)
        output_cap = _bounded_env_int("OPENAI_CLASSROOM_MAX_OUTPUT_TOKENS", 1_024, 256, 2_048)
        input_budget = TokenBudget(
            max_input_tokens=input_cap, reserve_prompt_overhead=True
        ).payload_budget(
            system_instruction=instruction, response_schema=VOLUNTEER_RESPONSE_SCHEMA
        ).max_input_tokens
        while estimate_tokens(json.dumps(payload, ensure_ascii=False)) > input_budget:
            reducible = [
                student_data for student_data in payload["students"]
                if len(student_data["personality"]) > 40
            ]
            if reducible:
                longest = max(reducible, key=lambda item: len(item["personality"]))
                value = longest["personality"]
                longest["personality"] = value[:max(40, int(len(value) * 0.75))].rstrip()
                continue
            question_value = payload["classroom_question"]
            if len(question_value) > 128:
                payload["classroom_question"] = question_value[:max(128, int(len(question_value) * 0.75))].rstrip()
                continue
            logger.warning("Classroom volunteer prompt exceeds configured input budget; using local decision")
            return self._local_classroom_volunteer_decisions(students, round_number=round_number)
        prompt = json.dumps(payload, ensure_ascii=False)
        max_output_tokens = min(output_cap, max(256, len(students) * 24))
        started = time.perf_counter()
        last_error: Exception | None = None
        self._last_latency_ms.update(ai_api=0.0, ai_calls=0)
        for _ in range(self.max_retries + 1):
            try:
                response = self._timed_generation(
                    model=self.model,
                    contents=prompt,
                    config={
                        "system_instruction": instruction,
                        "max_output_tokens": max_output_tokens,
                        "usage_category": "classroom_volunteers",
                        "response_mime_type": "application/json",
                        "response_schema": VOLUNTEER_RESPONSE_SCHEMA,
                    },
                )
                parsed = json.loads(response.text)
                decisions = parsed.get("decisions") if isinstance(parsed, dict) else None
                if not isinstance(decisions, list):
                    raise ValueError("classroom volunteer response did not contain decisions")
                result = {student_id: "NONE" for student_id in allowed}
                seen: set[str] = set()
                for decision in decisions:
                    if not isinstance(decision, dict):
                        raise ValueError("invalid classroom volunteer decision entry")
                    persona_id = decision.get("persona_id")
                    action = decision.get("action")
                    if persona_id not in allowed or persona_id in seen or action not in {"RAISE_HAND", "NONE"}:
                        raise ValueError("invalid or duplicate classroom volunteer decision")
                    seen.add(persona_id)
                    result[persona_id] = action
                self._last_latency_ms["classroom_volunteer_decision"] = round(
                    (time.perf_counter() - started) * 1000, 2
                )
                return result
            except Exception as error:
                last_error = error
                logger.warning("OpenAI classroom volunteer decision failed: %s", type(error).__name__)
                if getattr(error, "code", None) in (400, 401, 403, 404):
                    break
        self._last_latency_ms["classroom_volunteer_decision"] = round(
            (time.perf_counter() - started) * 1000, 2
        )
        logger.warning("Using local classroom volunteer fallback after OpenAI failure: %s", type(last_error).__name__ if last_error else "unknown")
        return self._local_classroom_volunteer_decisions(students, round_number=round_number)

    @staticmethod
    def _local_classroom_volunteer_decisions(
        students: list[Any], *, round_number: int = 0
    ) -> dict[str, str]:
        """Deterministic personality-aware fallback; rotation prevents a fixed volunteer set."""
        knowledge = {"beginner": 0.35, "basic": 0.45, "intermediate": 0.65, "advanced": 0.85, "expert": 0.95}
        result: dict[str, str] = {}
        total = max(1, len(students))
        for index, student in enumerate(students):
            level = knowledge.get(str(student.knowledge_level).casefold(), 0.6)
            rotation = (((round_number + index * 5) % total) / total) * 0.12
            personality = str(student.personality).casefold()
            personality_bias = 0.0
            if any(word in personality for word in ("curious", "confident", "outgoing", "eager")):
                personality_bias += 0.08
            if any(word in personality for word in ("quiet", "shy", "reserved", "reluctant")):
                personality_bias -= 0.08
            score = (
                0.40 * level
                + 0.28 * float(student.confidence)
                + 0.22 * float(student.willingness)
                + personality_bias
                + rotation
            )
            result[student.student_id] = "RAISE_HAND" if score >= 0.62 else "NONE"
        return result

    def generate_reply(self, request: BehaviorRequest) -> BehaviorResponse:
        self._last_latency_ms.update(academic_selection=0.0, ai_api=0.0, ai_calls=0, llm_generation=0.0)
        intent = classify_teacher_input(request.trainee_input)
        if request.name_only_address:
            self._last_latency_ms["llm_generation"] = 0.0
            language = infer_response_language(request.trainee_input, request.language)
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="Ano po, Ma'am?" if language == "Filipino" else "Yes, Ma'am!",
                emotion="respectful",
                action="NONE",
                speak=True,
                fallback=False,
            )
        if request.clarification_required:
            self._last_latency_ms["llm_generation"] = 0.0
            return self._clarification_reply(request)
        classroom = request.classroom_context
        if classroom:
            request = request.model_copy(update={"grade_level": classroom.grade})
        beyond_grade = (
            _is_beyond_grade_level(request.trainee_input, request.grade_level)
            and "THINKING" in request.supported_actions
            and "IDK" in request.supported_actions
        )
        if beyond_grade and classroom is None:
            self._last_latency_ms["llm_generation"] = 0.0
            return _grade_limited_reply(request)
        if intent.action != "NONE" and not intent.speak:
            if request.acknowledgment_required:
                self._last_latency_ms["llm_generation"] = 0.0
                language = infer_response_language(request.trainee_input, request.language)
                return BehaviorResponse(
                    persona_id=request.persona_id,
                    dialogue="Opo, Ma'am." if language == "Filipino" else (
                        "Yes po, Ma'am." if language == "Taglish" else "Yes, Ma'am."
                    ),
                    emotion="respectful",
                    action=intent.action,
                    speak=True,
                    fallback=False,
                )
            self._last_latency_ms["llm_generation"] = 0.0
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="",
                emotion="neutral",
                action=intent.action,
                speak=False,
                fallback=False,
            )

        academic_decision = None
        if (
            request.academic_behavior_enabled
            and "THINKING" in request.supported_actions
            and "IDK" in request.supported_actions
        ):
            academic_started = time.perf_counter()
            academic_decision = self.academic_controller.select_behavior(
                request.trainee_input,
                session_id=request.session_id,
                persona_id=request.persona_id,
                knowledge_level=request.knowledge_level,
                grade_level=request.grade_level,
            )
            self._last_latency_ms["academic_selection"] = round((time.perf_counter() - academic_started) * 1000, 2)
            request = request.model_copy(update={
                "academic_difficulty": academic_decision.difficulty.value,
                "academic_behavior": academic_decision.behavior.value,
            })
            logger.debug(
                "Academic behavior selected persona=%s difficulty=%s behavior=%s final_action_pending",
                request.persona_id,
                academic_decision.difficulty.value,
                academic_decision.behavior.value,
            )
            if academic_decision.behavior == SelectedBehavior.IDK and classroom is None:
                self._last_latency_ms["llm_generation"] = 0.0
                reply = self._idk_reply(request)
                logger.debug(
                    "Academic final response persona=%s difficulty=%s selected=IDK action=%s",
                    request.persona_id, academic_decision.difficulty.value, reply.action,
                )
                return reply

        def local_reply(error: Exception | None) -> BehaviorResponse:
            if beyond_grade:
                return _grade_limited_reply(request)
            return self._apply_academic_decision(self._fallback(request, error), request, academic_decision)

        instruction = SCOPED_BEHAVIOR_INSTRUCTIONS if classroom else BEHAVIOR_INSTRUCTIONS
        schema = SCOPED_RESPONSE_SCHEMA if classroom else BEHAVIOR_RESPONSE_SCHEMA
        llm_started = time.perf_counter()
        response_budget = self.budget.payload_budget(
            system_instruction=instruction,
            response_schema=schema,
        )
        payload = response_budget.prepare_payload(
            persona_id=request.persona_id,
            persona_name=request.persona_name,
            language=infer_response_language(request.trainee_input, request.language),
            trainee_input=request.trainee_input,
            knowledge_level=request.knowledge_level,
            confidence=request.confidence,
            willingness=request.willingness,
            grade_level=request.grade_level,
            persona_profile=request.persona_profile,
            scene_state=request.scene_state,
            session_summary=request.session_summary,
            recent_turns=request.recent_turns,
            supported_actions=request.supported_actions,
            academic_difficulty=request.academic_difficulty,
            academic_behavior=request.academic_behavior,
            classroom_context=classroom.prompt_context() if classroom else None,
        )
        payload_json = json.dumps(payload, ensure_ascii=False)
        full_prompt_estimate = (
            estimate_tokens(instruction)
            + estimate_tokens(json.dumps(schema, ensure_ascii=False))
            + estimate_tokens(payload_json)
            + 8
        )
        if self.budget.reserve_prompt_overhead and full_prompt_estimate > self.budget.max_input_tokens:
            self._last_latency_ms["llm_generation"] = 0.0
            logger.warning(
                "Behavior prompt exceeds configured input budget persona=%s estimated_tokens=%d limit=%d; using local fallback",
                request.persona_id,
                full_prompt_estimate,
                self.budget.max_input_tokens,
            )
            return local_reply(ValueError("prompt exceeds configured input budget"))

        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                response = self._timed_generation(
                    model=self.model,
                    contents=payload_json,
                    config={
                        "system_instruction": instruction,
                        "max_output_tokens": self.budget.max_output_tokens,
                        "usage_category": "student_behavior",
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                    },
                )
                self._last_latency_ms["llm_generation"] = round((time.perf_counter() - llm_started) * 1000, 2)
                data = json.loads(response.text)
                relation = data.pop("subject_relation", None) if classroom and isinstance(data, dict) else None
                result = BehaviorResponse.model_validate(data)
                if result.persona_id != request.persona_id or (classroom and relation not in SUBJECT_RELATIONS):
                    return local_reply(ValueError("invalid response persona or subject classification"))
                if classroom and relation == "UNRELATED":
                    reply = self._subject_redirect(request, classroom, intent)
                    reply.usage = self._usage(response)
                    return reply
                if beyond_grade or (academic_decision and academic_decision.behavior == SelectedBehavior.IDK):
                    reply = _grade_limited_reply(request) if beyond_grade else self._idk_reply(request)
                    reply.usage = self._usage(response)
                    return reply
                result = self._normalize_result(result, request, intent)
                if (
                    academic_decision is not None
                    and academic_decision.behavior == SelectedBehavior.THINKING
                    and result.dialogue
                    and "THINKING" in request.supported_actions
                ):
                    result = result.model_copy(update={"action": "THINKING"})
                logger.debug(
                    "Academic final response persona=%s difficulty=%s selected=%s action=%s",
                    request.persona_id,
                    academic_decision.difficulty.value if academic_decision else None,
                    academic_decision.behavior.value if academic_decision else None,
                    result.action,
                )
                result.usage = self._usage(response)
                self._last_latency_ms["llm_generation"] = round(
                    (time.perf_counter() - llm_started) * 1000, 2
                )
                return result
            except (json.JSONDecodeError, ValidationError) as error:
                logger.warning("OpenAI behavior response validation failed: %s", type(error).__name__)
                return local_reply(error)
            except Exception as error:  # Provider and parsing failures use the bounded fallback.
                logger.warning("OpenAI behavior request failed: %s", type(error).__name__)
                last_error = error
                if getattr(error, "code", None) in (400, 401, 403, 404):
                    break

        self._last_latency_ms["llm_generation"] = round(
            (time.perf_counter() - llm_started) * 1000, 2
        )
        return local_reply(last_error)

    def _timed_generation(self, **kwargs):
        started = time.perf_counter()
        self._last_latency_ms["ai_calls"] += 1
        try:
            return self._client().models.generate_content(**kwargs)
        finally:
            self._last_latency_ms["ai_api"] += round((time.perf_counter() - started) * 1000, 2)

    def latency_status(self) -> dict[str, float | None]:
        return dict(self._last_latency_ms)

    @staticmethod
    def _clarification_reply(request: BehaviorRequest) -> BehaviorResponse:
        language = infer_response_language(request.trainee_input, request.language)
        if language == "Filipino":
            dialogue = "Pasensya na po, hindi ko naintindihan. Maaari mo bang ulitin?"
        elif language == "Taglish":
            dialogue = "Sorry po, hindi ko naintindihan. Could you please repeat that?"
        else:
            dialogue = "Sorry, I did not understand. Could you please repeat that?"
        return BehaviorResponse(
            persona_id=request.persona_id,
            dialogue=dialogue,
            emotion="respectful",
            action="NONE",
            speak=True,
            fallback=False,
        )

    @staticmethod
    def _idk_reply(request: BehaviorRequest) -> BehaviorResponse:
        language = infer_response_language(request.trainee_input, request.language)
        if language == "Filipino":
            dialogue = "Hindi ko po alam, Ma'am."
        elif language == "Taglish":
            dialogue = "Sorry po, Ma'am. I'm not sure."
        else:
            dialogue = "I'm sorry, Ma'am. I don't know the answer."
        return BehaviorResponse(
            persona_id=request.persona_id,
            dialogue=dialogue,
            emotion="uncertain",
            action="IDK",
            speak=True,
            fallback=False,
        )

    @staticmethod
    def _apply_academic_decision(
        reply: BehaviorResponse,
        request: BehaviorRequest,
        decision: Any | None,
    ) -> BehaviorResponse:
        if decision is None:
            return reply
        if decision.behavior == SelectedBehavior.IDK:
            return BehaviorService._idk_reply(request)
        if (
            decision.behavior == SelectedBehavior.THINKING
            and reply.dialogue
            and reply.speak
            and "THINKING" in request.supported_actions
        ):
            return reply.model_copy(update={"action": "THINKING"})
        return reply

    @staticmethod
    def _subject_redirect(request: BehaviorRequest, classroom: ClassroomContext, intent: TeacherIntent) -> BehaviorResponse:
        language = infer_response_language(request.trainee_input, request.language)
        subject = classroom.subject_label
        options = {
            "Filipino": (
                f"Ibang paksa po iyon, Ma'am. {subject} po ang aralin natin ngayon. Maaari po nating balikan iyon mamaya?",
                f"Ma'am, puwede po bang bumalik muna tayo sa {subject}? Ibang aralin po ang tanong na iyon.",
                f"Sa ibang aralin po yata iyan. May tanong pa po ba kayo tungkol sa {subject}, Ma'am?",
            ),
            "Taglish": (
                f"Ma'am, ibang topic po iyon. Can we go back to our {subject} lesson?",
                f"That belongs in another class po. Puwede po nating balikan after {subject}?",
                f"Ma'am, {subject} po ang lesson namin ngayon. Could we try a question about that?",
            ),
            "English": (
                f"That's for a different subject, Ma'am. Could we go back to our {subject} lesson?",
                f"Could we save that for another lesson, Ma'am? We're working on {subject} right now.",
                f"I think that's a different class topic, Ma'am. Do you have a question about {subject}?",
            ),
        }.get(language)
        variants = options or (
            f"That's for a different subject, Ma'am. Could we go back to our {subject} lesson?",
        )
        fingerprint = f"{request.persona_id}|{request.trainee_input.casefold()}".encode()
        return BehaviorResponse(
            persona_id=request.persona_id, dialogue=variants[sha256(fingerprint).digest()[0] % len(variants)],
            emotion="respectful", action=intent.action, speak=True,
        )

    def _normalize_result(
        self,
        result: BehaviorResponse,
        request: BehaviorRequest,
        intent: TeacherIntent,
    ) -> BehaviorResponse:
        """Keep action and speech intent independent and consistent for Unreal."""
        autonomous_action = result.action if result.action in {"THINKING", "IDK"} and result.action in request.supported_actions else "NONE"
        action = intent.action if intent.action != "NONE" else autonomous_action
        speak = intent.speak
        dialogue = result.dialogue.strip() if speak else ""
        if speak and not dialogue:
            return self._fallback(request, ValueError("spoken response was empty"))
        return result.model_copy(update={"action": action, "speak": speak, "dialogue": dialogue})

    def _client(self) -> Any:
        if self.client is None:
            self.client = create_openai_client()
        return self.client

    @staticmethod
    def _usage(response: Any) -> TokenUsage | None:
        usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
        if usage is None:
            return None
        return TokenUsage(
            input_tokens=getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", getattr(usage, "prompt_token_count", 0))),
            output_tokens=getattr(usage, "output_tokens", getattr(usage, "completion_tokens", getattr(usage, "candidates_token_count", 0))),
            total_tokens=getattr(usage, "total_tokens", getattr(usage, "total_token_count", 0)),
        )

    def _fallback(
        self, request: BehaviorRequest, error: Exception | None
    ) -> BehaviorResponse:
        del error  # The response is intentionally safe and does not expose provider details.
        language = infer_response_language(request.trainee_input, request.language)
        if language == "Filipino":
            default_dialogue = "Pasensya na po, hindi ko naintindihan. Maaari mo bang ulitin?"
        elif language == "Taglish":
            default_dialogue = "Sorry po, hindi ko naintindihan. Could you please repeat that?"
        else:
            default_dialogue = "I am thinking about that. Could you explain it another way?"
        return BehaviorResponse(
            persona_id=request.persona_id,
            dialogue=(self.fallback_responses.get(request.persona_id, default_dialogue)
                      if classify_teacher_input(request.trainee_input).speak else ""),
            emotion="neutral",
            action=self._fallback_action(request.trainee_input),
            speak=classify_teacher_input(request.trainee_input).speak,
            fallback=True,
        )

    @staticmethod
    def _fallback_action(trainee_input: str) -> str:
        text = trainee_input.casefold()
        intent = classify_teacher_input(text)
        if intent.action != "NONE":
            return intent.action
        if "stop" in text and "talk" in text:
            return "STOP_TALKING"
        return "NONE"
