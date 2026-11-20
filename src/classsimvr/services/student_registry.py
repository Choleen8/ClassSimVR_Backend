from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from threading import RLock
from typing import Any


class StudentRegistryError(ValueError):
    pass


class DuplicateStudentError(StudentRegistryError):
    pass


class StudentNotFoundError(StudentRegistryError):
    pass


DEFAULT_STUDENT_ACTIONS = (
    "NONE", "STAND", "SIT", "TALK", "STOP_TALKING", "RAISE_HAND", "LOWER_HAND",
)
THINKING_STUDENT_ACTIONS = DEFAULT_STUDENT_ACTIONS + ("THINKING", "IDK")
KNOWLEDGE_LEVELS = ("ADVANCED", "INTERMEDIATE", "BEGINNER")
GRADE_LEVELS = tuple(f"Grade {number}" for number in range(1, 13)) + ("College",)


def normalize_knowledge_level(value: str) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in KNOWLEDGE_LEVELS else "INTERMEDIATE"


def normalize_grade_level(value: str | None) -> str | None:
    """Normalize an optional curriculum level for reusable grade-aware behavior."""
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    if normalized == "college":
        return "College"
    match = re.fullmatch(r"(?:grade\s*)?(1[0-2]|[1-9])", normalized)
    return f"Grade {int(match.group(1))}" if match else None


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _match_key(value: str) -> str:
    """Normalize speech punctuation without changing stored student IDs."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", without_accents)).strip()


def _contains_alias(normalized_text: str, alias: str) -> bool:
    """Match an alias without relying on Latin-only regex word boundaries."""
    padded_text = f" {normalized_text} "
    return f" {alias} " in padded_text


def _levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


_LEGACY_STUDENT_IDS = {"berto-eco": "robert-eco"}


def _canonical_student_id(value: str) -> str:
    normalized = _key(value)
    return _LEGACY_STUDENT_IDS.get(normalized, normalized)


@dataclass(frozen=True)
class StudentProfile:
    student_id: str
    name: str
    aliases: tuple[str, ...] = ()
    personality: str = "a respectful Filipino VR student"
    language: str = "English"
    voice: str | None = None
    initial_state: str = "engaged"
    knowledge_level: str = "intermediate"
    confidence: float = 0.6
    willingness: float = 0.5
    gender: str = "unspecified"
    supported_actions: tuple[str, ...] = DEFAULT_STUDENT_ACTIONS

    def normalized_aliases(self) -> tuple[str, ...]:
        values = [self.student_id, self.name, *self.aliases]
        return tuple(dict.fromkeys(_match_key(value) for value in values if value.strip()))


@dataclass
class StudentSessionState:
    current_state: str = "engaged"
    emotion: str = "neutral"
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StudentResolution:
    """Result of resolving a spoken student reference."""

    profile: StudentProfile | None
    ambiguous: bool = False
    matched_aliases: tuple[str, ...] = ()


DEFAULT_STUDENT_PROFILES = (
    StudentProfile(
        student_id="maria-sandoval",
        name="Maria Sandoval",
        aliases=("maria",),
        personality="Maria is a respectful Filipino VR student.",
        language="English",
        voice="fil-PH-BlessicaNeural",
        knowledge_level="ADVANCED",
        confidence=0.7,
        willingness=0.65,
    ),
    StudentProfile(
        student_id="robert-eco",
        name="Robert Eco",
        aliases=("robert", "berto", "robert eco", "berto eco"),
        personality="Robert is a thoughtful Filipino VR student.",
        language="English",
        voice="fil-PH-AngeloNeural",
        knowledge_level="ADVANCED",
        confidence=0.65,
        willingness=0.5,
    ),
    StudentProfile(
        student_id="ethan-luna",
        name="Ethan Luna",
        aliases=(
            "ethan", "ethan luna", "etan", "itan", "eitan", "ethen", "eithan", "eton",
        ),
        personality=(
            "Ethan is a friendly, curious, respectful Filipino VR student. "
            "He is moderately confident, an average student, knows common topics, "
            "and may struggle with difficult questions."
        ),
        language="English",
        voice="fil-PH-AngeloNeural",
        knowledge_level="INTERMEDIATE",
        confidence=0.6,
        willingness=0.6,
        gender="male",
        supported_actions=THINKING_STUDENT_ACTIONS,
    ),
    StudentProfile(
        student_id="andrea-guinto",
        name="Andrea Guinto",
        aliases=("andrea", "andreya", "andria", "andreah", "andrea guinto"),
        personality=(
            "Andrea is a friendly, respectful, slightly shy Filipino VR student. "
            "She is an intermediate student who may need time with difficult questions."
        ),
        language="English",
        voice="en-US-JennyNeural",
        knowledge_level="INTERMEDIATE",
        confidence=0.55,
        willingness=0.55,
        gender="female",
        supported_actions=THINKING_STUDENT_ACTIONS,
    ),
    StudentProfile(
        student_id="judy-castillo",
        name="Judy Castillo",
        aliases=("judy", "judi", "judy castillo", "جودي", "جو دی", "جو دي"),
        personality=(
            "Judy is a friendly, respectful, cheerful Filipino VR student. "
            "She is a beginner learner and may be slightly hesitant with difficult questions."
        ),
        language="English",
        voice="en-US-AriaNeural",
        knowledge_level="BEGINNER",
        confidence=0.45,
        willingness=0.55,
        gender="female",
        supported_actions=THINKING_STUDENT_ACTIONS,
    ),
    StudentProfile(
        student_id="francis-reyes",
        name="Francis Reyes",
        aliases=("francis", "francis reyes", "fransis"),
        personality="Francis is a friendly, respectful, cooperative Filipino VR student who is reasonably confident.",
        language="English",
        voice="en-US-GuyNeural",
        knowledge_level="INTERMEDIATE",
        confidence=0.65,
        willingness=0.7,
        gender="male",
        supported_actions=THINKING_STUDENT_ACTIONS,
    ),
)


class StudentRegistry:
    """Thread-safe runtime registry for dynamic learner profiles and state."""

    _CONVERSATION_HISTORY_LIMIT = 12
    HAND_RAISE_TIMEOUT_SECONDS = 10.0

    def __init__(self, profiles: tuple[StudentProfile, ...] = ()) -> None:
        self._lock = RLock()
        self._profiles: dict[str, StudentProfile] = {}
        self._aliases: dict[str, str] = {}
        self._states: dict[str, dict[str, StudentSessionState]] = {}
        self._classroom_questions: dict[str, str] = {}
        self._selected_grade_levels: dict[str, str] = {}
        self._volunteer_rounds: dict[str, int] = {}
        for profile in profiles:
            self.register(profile)

    @classmethod
    def with_defaults(cls) -> "StudentRegistry":
        registry = cls(DEFAULT_STUDENT_PROFILES)
        configured = os.getenv("CLASSSIMVR_STUDENTS_JSON", "").strip()
        if configured:
            registry.load_json(configured, replace=False)
        return registry

    def register(self, profile: StudentProfile) -> StudentProfile:
        student_id = _canonical_student_id(profile.student_id)
        if not student_id or not profile.name.strip():
            raise StudentRegistryError("student_id and name must not be blank")
        normalized = StudentProfile(
            student_id=student_id,
            name=profile.name.strip(),
            aliases=tuple(alias.strip() for alias in profile.aliases if alias.strip()),
            personality=profile.personality.strip(),
            language=profile.language.strip() or "English",
            voice=profile.voice.strip() if profile.voice else None,
            initial_state=profile.initial_state.strip() or "engaged",
            knowledge_level=normalize_knowledge_level(profile.knowledge_level),
            confidence=min(1.0, max(0.0, float(profile.confidence))),
            willingness=min(1.0, max(0.0, float(profile.willingness))),
            gender=profile.gender.strip() or "unspecified",
            supported_actions=tuple(dict.fromkeys(profile.supported_actions)),
        )
        with self._lock:
            if student_id in self._profiles:
                raise DuplicateStudentError(f"student '{student_id}' is already registered")
            for alias in normalized.normalized_aliases():
                owner = self._aliases.get(alias)
                if owner is not None:
                    raise DuplicateStudentError(f"student alias '{alias}' is already registered")
            self._profiles[student_id] = normalized
            for alias in normalized.normalized_aliases():
                self._aliases[alias] = student_id
        return normalized

    def list_students(self) -> list[StudentProfile]:
        with self._lock:
            return list(self._profiles.values())

    def set_selected_grade_level(self, session_id: str, grade_level: str | None) -> str | None:
        """Set the shared curriculum level for one classroom session."""
        normalized = normalize_grade_level(grade_level)
        with self._lock:
            key = str(session_id)
            if normalized is None:
                self._selected_grade_levels.pop(key, None)
            else:
                self._selected_grade_levels[key] = normalized
            return normalized

    def get_selected_grade_level(self, session_id: str) -> str | None:
        with self._lock:
            return self._selected_grade_levels.get(str(session_id))

    def get(self, student_id: str) -> StudentProfile:
        with self._lock:
            try:
                return self._profiles[_canonical_student_id(student_id)]
            except KeyError as error:
                raise StudentNotFoundError(f"student '{student_id}' was not found") from error

    def resolve(self, text: str) -> StudentProfile | None:
        return self.resolve_with_status(text).profile

    def resolve_with_status(self, text: str) -> StudentResolution:
        normalized_text = _match_key(text)
        with self._lock:
            matches = [
                (alias, self._profiles[student_id])
                for alias, student_id in self._aliases.items()
                if _contains_alias(normalized_text, alias)
            ]
            if not matches:
                matches = self._fuzzy_matches(normalized_text)
        if not matches:
            return StudentResolution(profile=None)
        longest = max(len(alias) for alias, _ in matches)
        top_matches = [(alias, profile) for alias, profile in matches if len(alias) == longest]
        profiles = {profile.student_id: profile for _, profile in top_matches}
        aliases = tuple(alias for alias, _ in top_matches)
        if len(profiles) != 1:
            return StudentResolution(profile=None, ambiguous=True, matched_aliases=aliases)
        return StudentResolution(profile=next(iter(profiles.values())), matched_aliases=aliases)

    def _fuzzy_matches(self, normalized_text: str) -> list[tuple[str, StudentProfile]]:
        """Return only high-confidence single-token speech-name matches.

        Whisper name errors are common, but broad fuzzy matching can redirect a
        command to the wrong student. Restrict fuzzy matching to name-like
        aliases of at least four characters, require a close edit similarity,
        and keep all tied best candidates so the caller can reject ambiguity.
        """
        tokens = normalized_text.split()
        candidates: list[tuple[float, str, StudentProfile]] = []
        for alias, student_id in self._aliases.items():
            if " " in alias or len(alias) < 4:
                continue
            profile = self._profiles[student_id]
            for token in tokens:
                if token == alias or len(token) < 4:
                    continue
                score = SequenceMatcher(None, token, alias).ratio()
                edit_distance = _levenshtein_distance(token, alias)
                max_distance = 1 if len(alias) <= 5 else 2
                if score >= 0.92 and edit_distance <= max_distance:
                    candidates.append((score, token, profile))

        if not candidates:
            return []
        best_score = max(score for score, _, _ in candidates)
        # Treat near-ties across different students as ambiguous rather than
        # guessing. The small margin still permits one clearly best match.
        best = [candidate for candidate in candidates if best_score - candidate[0] <= 0.03]
        profiles = {profile.student_id: profile for _, _, profile in best}
        if len(profiles) != 1:
            return [(token, profile) for _, token, profile in best]
        return [(token, next(iter(profiles.values()))) for _, token, _ in best[:1]]

    def get_session_state(self, session_id: str, student_id: str) -> StudentSessionState:
        profile = self.get(student_id)
        with self._lock:
            session_states = self._states.setdefault(str(session_id), {})
            return session_states.setdefault(
                profile.student_id,
                StudentSessionState(current_state=profile.initial_state),
            )

    def update_session_state(
        self,
        session_id: str,
        student_id: str,
        *,
        current_state: str | None = None,
        emotion: str | None = None,
        summary: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> StudentSessionState:
        state = self.get_session_state(session_id, student_id)
        with self._lock:
            if current_state is not None:
                state.current_state = current_state
            if emotion is not None:
                state.emotion = emotion
            if summary is not None:
                state.summary = summary
            if data:
                state.data.update(data)
            return state

    def get_conversation_memory(self, session_id: str, student_id: str) -> dict[str, Any]:
        """Return bounded recent turns plus durable recall metadata for one student session."""
        state = self.get_session_state(session_id, student_id)
        with self._lock:
            return {
                "first_teacher_question": str(state.data.get("first_teacher_question", "")),
                "summary": str(state.data.get("conversation_summary", "")),
                "recent_turns": [dict(turn) for turn in state.data.get("conversation_history", [])],
            }

    def record_conversation_turn(
        self,
        session_id: str,
        student_id: str,
        teacher_text: str,
        student_text: str,
    ) -> StudentSessionState:
        """Persist an exact teacher/student exchange without sharing it across students."""
        state = self.get_session_state(session_id, student_id)
        teacher_text = teacher_text.strip()
        student_text = student_text.strip()
        if not teacher_text:
            return state
        with self._lock:
            history = [dict(turn) for turn in state.data.get("conversation_history", [])]
            if not state.data.get("first_teacher_question"):
                state.data["first_teacher_question"] = teacher_text
            history.append({"speaker": "teacher", "text": teacher_text[:4000]})
            if student_text:
                history.append({"speaker": "student", "text": student_text[:4000]})
            older = history[:-self._CONVERSATION_HISTORY_LIMIT]
            first_question = state.data["first_teacher_question"]
            summary = f"first_teacher_question={first_question}"
            if older:
                summary += "; earlier_turns=" + " | ".join(
                    f"{turn['speaker']}: {turn['text']}" for turn in older
                )
            state.data["conversation_history"] = history[-self._CONVERSATION_HISTORY_LIMIT :]
            state.data["conversation_summary"] = summary[:12000]
            state.data["conversation_turn_count"] = int(state.data.get("conversation_turn_count", 0)) + 1
            return state

    def reset_session(self, session_id: str, student_id: str | None = None) -> None:
        """Explicitly clear conversation memory while retaining registered profiles."""
        with self._lock:
            if not student_id:
                self._classroom_questions.pop(str(session_id), None)
                self._volunteer_rounds.pop(str(session_id), None)
            session_states = self._states.get(str(session_id))
            if not session_states:
                return
            if student_id:
                states = [session_states[student_id]] if student_id in session_states else []
            else:
                states = list(session_states.values())
            for state in states:
                for key in (
                    "first_teacher_question",
                    "conversation_summary",
                    "conversation_history",
                    "conversation_turn_count",
                ):
                    state.data.pop(key, None)

    def record_classroom_question(self, session_id: str, question: str) -> None:
        """Remember the latest classroom question for a later volunteer invitation."""
        question = question.strip()
        if question:
            with self._lock:
                self._classroom_questions[str(session_id)] = question[:4000]

    def get_classroom_question(self, session_id: str) -> str:
        with self._lock:
            return self._classroom_questions.get(str(session_id), "")

    def next_volunteer_round(self, session_id: str) -> int:
        with self._lock:
            key = str(session_id)
            round_number = self._volunteer_rounds.get(key, 0) + 1
            self._volunteer_rounds[key] = round_number
            return round_number

    def mark_hand_raised(self, session_id: str, student_id: str, *, round_number: int) -> bool:
        """Mark one student as waiting with a timestamp; return False if already raised."""
        state = self.get_session_state(session_id, student_id)
        with self._lock:
            if state.data.get("hand_raised"):
                return False
            state.data.update({
                "hand_raised": True,
                "hand_raised_at": time.monotonic(),
                "last_action": "RAISE_HAND",
                "volunteer_round": round_number,
            })
            return True

    def lower_raised_hands(
        self,
        session_id: str,
        *,
        keep_student_id: str | None = None,
        reason: str = "lifecycle",
    ) -> list[str]:
        """Lower all raised hands except an optional selected student."""
        keep = _canonical_student_id(keep_student_id) if keep_student_id else None
        lowered: list[str] = []
        with self._lock:
            session_states = self._states.get(str(session_id), {})
            for student_id, state in session_states.items():
                if student_id == keep or not state.data.get("hand_raised"):
                    continue
                state.data.update({
                    "hand_raised": False,
                    "hand_raised_at": None,
                    "last_action": "LOWER_HAND",
                    "hand_lowered_reason": reason,
                })
                lowered.append(student_id)
        return lowered

    def expire_hand_raises(
        self,
        session_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> list[str]:
        """Lower hands that have waited beyond the configured timeout."""
        timeout = self.HAND_RAISE_TIMEOUT_SECONDS if timeout_seconds is None else max(0.0, timeout_seconds)
        now = time.monotonic()
        expired: list[str] = []
        with self._lock:
            session_states = self._states.get(str(session_id), {})
            for student_id, state in session_states.items():
                raised_at = state.data.get("hand_raised_at")
                if not state.data.get("hand_raised") or not isinstance(raised_at, (int, float)):
                    continue
                if now - float(raised_at) < timeout:
                    continue
                state.data.update({
                    "hand_raised": False,
                    "hand_raised_at": None,
                    "last_action": "LOWER_HAND",
                    "hand_lowered_reason": "timeout",
                })
                expired.append(student_id)
        return expired

    def load_json(self, raw: str, *, replace: bool = False) -> list[StudentProfile]:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = payload.get("students", [])
        if not isinstance(payload, list):
            raise StudentRegistryError("student configuration must be a JSON list")
        if replace:
            with self._lock:
                self._profiles.clear()
                self._aliases.clear()
        loaded: list[StudentProfile] = []
        for item in payload:
            if not isinstance(item, dict):
                raise StudentRegistryError("each student configuration must be an object")
            raw_student_id = str(item["student_id"])
            canonical_student_id = _canonical_student_id(raw_student_id)
            if (
                _key(raw_student_id) != canonical_student_id
                and canonical_student_id in self._profiles
                and not replace
            ):
                loaded.append(self.get(canonical_student_id))
                continue
            profile = StudentProfile(
                student_id=canonical_student_id,
                name=str(item["name"]),
                aliases=tuple(str(alias) for alias in item.get("aliases", [])),
                personality=str(item.get("personality", "a respectful Filipino VR student")),
                language=str(item.get("language", "English")),
                voice=str(item["voice"]) if item.get("voice") else None,
                initial_state=str(item.get("initial_state", "engaged")),
                knowledge_level=normalize_knowledge_level(item.get("knowledge_level", "INTERMEDIATE")),
                confidence=float(item.get("confidence", 0.6)),
                willingness=float(item.get("willingness", 0.5)),
                gender=str(item.get("gender", "unspecified")),
                supported_actions=tuple(
                    str(action) for action in item.get("supported_actions", DEFAULT_STUDENT_ACTIONS)
                ),
            )
            loaded.append(self.register(profile))
        return loaded
