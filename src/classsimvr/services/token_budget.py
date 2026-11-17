from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


def estimate_tokens(value: str) -> int:
    """Estimate tokens conservatively enough for bounded prompt construction."""
    return max(1, math.ceil(len(value) / 4))


@dataclass(frozen=True)
class TokenBudget:
    max_input_tokens: int = 1_200
    max_output_tokens: int = 180
    recent_turn_limit: int = 6

    def prepare_context(
        self,
        *,
        persona_profile: str,
        scene_state: str,
        session_summary: str,
        recent_turns: list[dict[str, str]],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "persona_profile": persona_profile,
            "scene_state": scene_state,
            "session_summary": session_summary,
            "recent_turns": [
                dict(turn) for turn in recent_turns[-self.recent_turn_limit :]
            ],
        }

        while estimate_tokens(json.dumps(context, ensure_ascii=False)) > self.max_input_tokens:
            candidates = [
                ("persona_profile", context["persona_profile"]),
                ("scene_state", context["scene_state"]),
                ("session_summary", context["session_summary"]),
            ]
            candidates.extend(
                (f"recent_turns.{index}.text", turn.get("text", ""))
                for index, turn in enumerate(context["recent_turns"])
            )
            key, value = max(candidates, key=lambda item: len(item[1]))
            if len(value) <= 32:
                break
            shortened = value[: max(32, int(len(value) * 0.8))].rstrip()
            if key.startswith("recent_turns."):
                _, index, _ = key.split(".")
                context["recent_turns"][int(index)]["text"] = shortened
            else:
                context[key] = shortened

        return context

    def prepare_payload(
        self,
        *,
        persona_id: str,
        persona_name: str,
        language: str,
        trainee_input: str,
        persona_profile: str,
        scene_state: str,
        session_summary: str,
        recent_turns: list[dict[str, str]],
        knowledge_level: str = "INTERMEDIATE",
        confidence: float = 0.6,
        willingness: float = 0.5,
        supported_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        fixed = {
            "persona_id": persona_id,
            "persona_name": persona_name,
            "language": language,
            "trainee_input": trainee_input,
            "knowledge_level": knowledge_level,
            "confidence": confidence,
            "willingness": willingness,
        }
        if supported_actions is not None:
            fixed["supported_actions"] = list(supported_actions)
        context_budget = max(
            1,
            self.max_input_tokens - estimate_tokens(json.dumps(fixed, ensure_ascii=False)),
        )
        context = TokenBudget(
            max_input_tokens=context_budget,
            max_output_tokens=self.max_output_tokens,
            recent_turn_limit=self.recent_turn_limit,
        ).prepare_context(
            persona_profile=persona_profile,
            scene_state=scene_state,
            session_summary=session_summary,
            recent_turns=recent_turns,
        )
        payload: dict[str, Any] = {**fixed, "context": context}

        while estimate_tokens(json.dumps(payload, ensure_ascii=False)) > self.max_input_tokens:
            candidates: list[tuple[tuple[str, ...], str]] = [
                (("persona_id",), payload["persona_id"]),
                (("persona_name",), payload["persona_name"]),
                (("language",), payload["language"]),
                (("trainee_input",), payload["trainee_input"]),
                (("context", "persona_profile"), context["persona_profile"]),
                (("context", "scene_state"), context["scene_state"]),
                (("context", "session_summary"), context["session_summary"]),
            ]
            candidates.extend(
                (("context", "recent_turns", str(index), "text"), turn.get("text", ""))
                for index, turn in enumerate(context["recent_turns"])
            )
            path, value = max(candidates, key=lambda item: len(item[1]))
            if len(value) <= 8:
                break
            target: Any = payload
            for part in path[:-1]:
                target = target[int(part)] if part.isdigit() else target[part]
            target[path[-1]] = value[: max(8, int(len(value) * 0.8))].rstrip()

        return payload
