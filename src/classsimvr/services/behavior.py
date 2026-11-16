from __future__ import annotations

import json
import os
import re
import unicodedata
import time
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .contracts import BehaviorRequest, BehaviorResponse, TokenUsage
from .gemini_client import create_gemini_client
from .token_budget import TokenBudget

logger = logging.getLogger(__name__)

BEHAVIOR_INSTRUCTIONS = """You are a virtual learner in a Philippine classroom simulation.
Respond only with a JSON object containing exactly these fields:
persona_id, dialogue, emotion, action, speak.
Stay in character, answer the trainee's latest input, and keep dialogue under 40 words.
Use the requested language when possible. Do not include markdown or extra commentary.
The action must be exactly one of: NONE, STAND, SIT, TALK, STOP_TALKING, RAISE_HAND, LOWER_HAND.
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
]


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
    r"\btell me\b",
    r"\bdescribe\b",
    r"\bdefine\b",
    r"\bano\b",
    r"\bbakit\b",
    r"\bpaano\b",
    r"\bsino\b",
    r"\b(is|are|does|do|can|will|should)\b",
)

_CONVERSATIONAL_PATTERNS = (
    r"\bintroduce yourself\b",
    r"\bintroduce your self\b",
    r"\btell me about yourself\b",
    r"\bwho are you\b",
    r"\bmagpakilala\b",
    r"\bpakilala ka\b",
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
        max_retries: int = 2,
        fallback_responses: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.model = model or os.getenv("GEMINI_BEHAVIOR_MODEL", "gemini-3.5-flash-lite")
        self.budget = budget or TokenBudget()
        self.max_retries = max(0, max_retries)
        self.fallback_responses = fallback_responses or {}
        self._last_latency_ms: dict[str, float | None] = {
            "llm_generation": None,
            "classroom_volunteer_decision": None,
        }

    def decide_classroom_volunteers(
        self,
        question: str,
        students: list[Any],
        *,
        round_number: int = 0,
    ) -> dict[str, str]:
        """Make one validated, independent volunteer decision for every student."""
        if not question.strip() or not students:
            return {}
        allowed = {student.student_id: student for student in students}
        prompt = json.dumps({
            "classroom_question": question.strip()[:4000],
            "round_number": round_number,
            "students": [
                {
                    "persona_id": student.student_id,
                    "name": student.name,
                    "personality": student.personality,
                    "knowledge_level": student.knowledge_level,
                    "confidence": student.confidence,
                    "willingness": student.willingness,
                }
                for student in students
            ],
        }, ensure_ascii=False)
        instruction = (
            "You are evaluating classroom participation. For every listed student, make an "
            "independent decision based on the question, knowledge_level, personality, "
            "confidence, willingness, and round_number. Zero, one, or many may volunteer; "
            "do not choose randomly and do not make everyone volunteer. Return only JSON with "
            "decisions, one entry per listed persona_id. action must be exactly RAISE_HAND or "
            "NONE. Volunteers remain seated and silent; do not generate dialogue."
        )
        started = time.perf_counter()
        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                response = self._client().models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={
                        "system_instruction": instruction,
                        "max_output_tokens": max(256, min(self.budget.max_output_tokens, 1024)),
                        "response_mime_type": "application/json",
                        "response_schema": {
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
                        },
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
                logger.warning("Classroom volunteer batch decision failed: %s", error)
        self._last_latency_ms["classroom_volunteer_decision"] = round(
            (time.perf_counter() - started) * 1000, 2
        )
        logger.warning("Using local classroom volunteer fallback after provider failure: %s", last_error)
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
        intent = classify_teacher_input(request.trainee_input)
        if request.name_only_address:
            self._last_latency_ms["llm_generation"] = 0.0
            language = infer_response_language(request.trainee_input, request.language)
            return BehaviorResponse(
                persona_id=request.persona_id,
                dialogue="Ano po, Ma'am?" if language == "Filipino" else "Yes, Ma'am?",
                emotion="respectful",
                action="NONE",
                speak=True,
                fallback=False,
            )
        if request.clarification_required:
            self._last_latency_ms["llm_generation"] = 0.0
            return self._clarification_reply(request)
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

        llm_started = time.perf_counter()
        payload = self.budget.prepare_payload(
            persona_id=request.persona_id,
            persona_name=request.persona_name,
            language=infer_response_language(request.trainee_input, request.language),
            trainee_input=request.trainee_input,
            persona_profile=request.persona_profile,
            scene_state=request.scene_state,
            session_summary=request.session_summary,
            recent_turns=request.recent_turns,
        )
        payload_json = json.dumps(payload, ensure_ascii=False)

        last_error: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                response = self._client().models.generate_content(
                    model=self.model,
                    contents=payload_json,
                    config={
                        "system_instruction": BEHAVIOR_INSTRUCTIONS,
                        "max_output_tokens": self.budget.max_output_tokens,
                        "response_mime_type": "application/json",
                        "response_schema": {
                            "type": "OBJECT",
                            "properties": {
                                "persona_id": {"type": "STRING"},
                                "dialogue": {"type": "STRING"},
                                "emotion": {"type": "STRING"},
                                "action": {"type": "STRING", "enum": ACTION_VALUES},
                                "speak": {"type": "BOOLEAN"},
                            },
                            "required": ["persona_id", "dialogue", "emotion", "action", "speak"],
                        },
                    },
                )
                result = BehaviorResponse.model_validate(json.loads(response.text))
                if result.persona_id != request.persona_id:
                    return self._fallback(request, ValueError("model returned the wrong persona"))
                result = self._normalize_result(result, request, intent)
                result.usage = self._usage(response)
                self._last_latency_ms["llm_generation"] = round(
                    (time.perf_counter() - llm_started) * 1000, 2
                )
                return result
            except (json.JSONDecodeError, ValidationError) as error:
                print(f"Gemini behavior response validation failed: {type(error).__name__}: {error}")
                return self._fallback(request, error)
            except Exception as error:  # Provider and parsing failures use the bounded fallback.
                print(f"Gemini behavior request failed: {type(error).__name__}: {error}")
                last_error = error

        self._last_latency_ms["llm_generation"] = round(
            (time.perf_counter() - llm_started) * 1000, 2
        )
        return self._fallback(request, last_error)

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
    def _normalize_result(
        result: BehaviorResponse,
        request: BehaviorRequest,
        intent: TeacherIntent,
    ) -> BehaviorResponse:
        """Keep action and speech intent independent and consistent for Unreal."""
        action = intent.action if intent.action != "NONE" else "NONE"
        speak = intent.speak
        dialogue = result.dialogue.strip() if speak else ""
        if speak and not dialogue:
            return BehaviorService._fallback(request, ValueError("spoken response was empty"))
        return result.model_copy(update={"action": action, "speak": speak, "dialogue": dialogue})

    def _client(self) -> Any:
        if self.client is None:
            self.client = create_gemini_client()
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
