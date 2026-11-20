from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, fields
from enum import Enum

from .student_registry import normalize_grade_level

logger = logging.getLogger(__name__)


class Difficulty(str, Enum):
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"
    VERY_HARD = "VERY_HARD"


class SelectedBehavior(str, Enum):
    ANSWER = "ANSWER"
    THINKING = "THINKING"
    IDK = "IDK"


@dataclass(frozen=True)
class AcademicAbilityConfig:
    """Configurable starting probabilities for an intermediate learner."""

    easy_answer: float = 0.90
    easy_thinking: float = 0.08
    easy_idk: float = 0.02
    moderate_answer: float = 0.55
    moderate_thinking: float = 0.35
    moderate_idk: float = 0.10
    hard_answer: float = 0.15
    hard_thinking: float = 0.35
    hard_idk: float = 0.50
    very_hard_answer: float = 0.00
    very_hard_thinking: float = 0.10
    very_hard_idk: float = 0.90

    @classmethod
    def from_environment(cls) -> "AcademicAbilityConfig":
        values: dict[str, float] = {}
        defaults = cls()
        for field in fields(cls):
            raw = os.getenv(f"CLASSSIMVR_ACADEMIC_{field.name.upper()}")
            if raw is None:
                continue
            try:
                values[field.name] = max(0.0, float(raw))
            except ValueError:
                logger.warning("Ignoring invalid academic probability %s=%r", field.name, raw)
        return cls(**{field.name: values.get(field.name, getattr(defaults, field.name)) for field in fields(cls)})

    def probabilities(self, difficulty: Difficulty, knowledge_level: str) -> tuple[float, float, float]:
        prefix = difficulty.value.casefold()
        answer = getattr(self, f"{prefix}_answer")
        thinking = getattr(self, f"{prefix}_thinking")
        idk = getattr(self, f"{prefix}_idk")
        level = str(knowledge_level or "INTERMEDIATE").upper()
        if level == "ADVANCED":
            answer += 0.15
            thinking = max(0.0, thinking - 0.05)
            idk = max(0.0, idk - 0.10)
        elif level == "BEGINNER":
            answer = max(0.0, answer - 0.20)
            thinking += 0.10
            idk += 0.10
        total = answer + thinking + idk
        return (answer / total, thinking / total, idk / total) if total else (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class AcademicDecision:
    difficulty: Difficulty
    behavior: SelectedBehavior
    is_unknowable: bool
    sample: float
    probabilities: tuple[float, float, float]


_PRIVATE_INFORMATION_PATTERNS = (
    r"\bwhat (?:number|am i) (?:am i )?thinking\b",
    r"\bwhat (?:number|am i) (?:am i )?thinking of\b",
    r"\bwhat did i eat(?: for (?:breakfast|lunch|dinner))?(?: yesterday| today| this morning)\b",
    r"\bwhat was i wearing(?: yesterday| today| earlier)\b",
    r"\bwhat(?: is| was) inside my bag\b",
    r"\bwhat(?:'s| is) my (?:password|pin|secret|exact location)\b",
    r"\b(?:read|guess) my mind\b",
    r"\bwhat will happen to me tomorrow\b",
)

_ADVANCED_DOMAIN_MARKERS = {
    "quantum", "entanglement", "relativity", "tensor", "schrodinger", "calculus",
    "differential", "thermodynamics", "eigenvalue", "proof", "derive", "integral",
}


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


class AcademicAbilityController:
    """Select a stable academic behavior before the response model is called."""

    def __init__(self, config: AcademicAbilityConfig | None = None) -> None:
        self.config = config or AcademicAbilityConfig.from_environment()

    def classify(self, question: str, grade_level: str | None = None) -> Difficulty:
        normalized = " ".join(_normalized_words(question))
        words = normalized.split()
        if any(re.search(pattern, normalized) for pattern in _PRIVATE_INFORMATION_PATTERNS):
            return Difficulty.VERY_HARD

        score = 0
        if len(words) >= 16:
            score += 1
        if len(words) >= 24:
            score += 1
        if len(re.findall(r"\b(?:and then|then|while|after|before)\b", normalized)):
            score += 1
        if len(re.findall(r"\b(?:compare|contrast|evaluate|justify|prove|derive|analyze)\b", normalized)):
            score += 2
        if any(word in _ADVANCED_DOMAIN_MARKERS for word in words):
            score += 2
        if normalize_grade_level(grade_level) not in (None, "College"):
            grade_number = int(normalize_grade_level(grade_level).split()[1])
            if grade_number <= 10 and any(word in _ADVANCED_DOMAIN_MARKERS for word in words):
                score += 2

        if score >= 4:
            return Difficulty.VERY_HARD
        if score >= 2:
            return Difficulty.HARD
        if score >= 1 or any(word in {"why", "how", "explain", "describe", "identify"} for word in words):
            return Difficulty.MODERATE
        return Difficulty.EASY

    def select_behavior(
        self,
        question: str,
        *,
        session_id: str,
        persona_id: str,
        knowledge_level: str,
        grade_level: str | None,
    ) -> AcademicDecision:
        difficulty = self.classify(question, grade_level)
        is_unknowable = any(
            re.search(pattern, " ".join(_normalized_words(question)))
            for pattern in _PRIVATE_INFORMATION_PATTERNS
        )
        probabilities = self.config.probabilities(difficulty, knowledge_level)
        digest = hashlib.sha256(
            f"{session_id}|{persona_id}|{' '.join(_normalized_words(question))}|{difficulty.value}".encode()
        ).digest()
        sample = int.from_bytes(digest[:8], "big") / float(2**64)
        if is_unknowable:
            behavior = SelectedBehavior.IDK
        elif sample < probabilities[2]:
            behavior = SelectedBehavior.IDK
        elif sample < probabilities[2] + probabilities[1]:
            behavior = SelectedBehavior.THINKING
        else:
            behavior = SelectedBehavior.ANSWER
        decision = AcademicDecision(difficulty, behavior, is_unknowable, sample, probabilities)
        logger.debug(
            "Academic decision session=%s persona=%s difficulty=%s ability=%s behavior=%s sample=%.4f probabilities=%s grade=%s",
            session_id, persona_id, difficulty.value, knowledge_level, behavior.value,
            sample, probabilities, grade_level or "UNSELECTED",
        )
        return decision
