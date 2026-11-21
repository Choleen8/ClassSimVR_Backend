from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
import unicodedata

from .behavior import BehaviorService, classify_teacher_input
from .contracts import BehaviorRequest, BehaviorResponse
from .tts import TextToSpeechService
from .student_registry import StudentProfile, StudentRegistry


StudentRoute = StudentProfile
logger = logging.getLogger(__name__)

_ADDRESS_STOP_WORDS = {
    "a", "all", "an", "and", "ano", "are", "bang", "bakit", "can", "class", "classroom",
    "could", "down", "everyone", "explain", "hand", "how", "iba", "it", "lahat", "lower",
    "me", "na", "please", "raise", "sit", "sino", "stand", "students", "tell", "the", "to",
    "tumayo", "umupo", "up", "upo", "what", "when", "where", "who", "why", "will", "would",
    "you", "ka", "mo", "kayo", "pwede", "puwede", "ng", "ngayon", "than", "down",
}
_DIRECT_ADDRESS_CUES = {
    "stand", "up", "sit", "down", "please", "tumayo", "tayo", "umupo", "upo",
    "maupo", "introduce", "explain", "tell", "magpakilala", "pakilala", "raise", "lower",
    "itaas", "ibaba",
}


@dataclass(frozen=True)
class CommandResult:
    target: str
    responses: list[BehaviorResponse]


DEFAULT_STUDENTS = StudentRegistry.with_defaults().list_students()


class MicrophoneCommandRouter:
    def __init__(
        self,
        *,
        behavior: BehaviorService,
        tts: TextToSpeechService,
        registry: StudentRegistry | None = None,
        students: tuple[StudentRoute, ...] | list[StudentRoute] | None = None,
    ) -> None:
        self.behavior = behavior
        self.tts = tts
        self.registry = registry or StudentRegistry(tuple(students or DEFAULT_STUDENTS))
        self._last_latency_ms = {
            "command_recognition": None,
            "student_name_detection": None,
            "student_routing": None,
            "conversation_memory_retrieval": None,
            "llm_generation": None,
            "classroom_volunteer_decision": None,
            "tts_generation": None,
            "response_queue": None,
            "response_delivery": None,
            "total_response": None,
        }

    def handle(
        self,
        transcript: str,
        *,
        session_id: str,
        scene_state: str = "",
        session_summary: str = "",
        recent_turns: list[dict[str, str]] | None = None,
        default_persona_id: str = "maria-sandoval",
        selected_grade_level: str | None = None,
    ) -> CommandResult:
        pipeline_started = time.perf_counter()
        self._last_latency_ms = dict.fromkeys(self._last_latency_ms)
        lifecycle_responses = self._lower_hand_responses(
            session_id,
            self.registry.expire_hand_raises(session_id),
        )
        command_started = time.perf_counter()
        intent = classify_teacher_input(transcript)
        self._last_latency_ms["command_recognition"] = round(
            (time.perf_counter() - command_started) * 1000, 2
        )
        routing_started = time.perf_counter()
        if self._is_autonomous_hand_raise_request(transcript):
            lowered = self._lower_hand_responses(
                session_id,
                self.registry.lower_raised_hands(session_id, reason="new_invitation"),
            )
            result = self._handle_classroom_invitation(session_id, invitation_text=transcript)
            result = CommandResult(
                target=result.target,
                responses=lifecycle_responses + lowered + result.responses,
            )
            self._last_latency_ms["total_response"] = round(
                (time.perf_counter() - pipeline_started) * 1000, 2
            )
            return result
        # A teacher may invite volunteers, but cannot directly force a student
        # to raise a hand. Treat direct raise-hand wording as a no-op.
        if self._is_direct_raise_hand_command(transcript):
            logger.info("Ignored direct raise-hand command transcript=%r", transcript)
            return CommandResult(
                target="classroom" if lifecycle_responses else "none",
                responses=lifecycle_responses,
            )
        target_students = self._targets(transcript, default_persona_id)
        self._last_latency_ms["student_routing"] = round(
            (time.perf_counter() - routing_started) * 1000, 2
        )
        if intent.speak:
            self.registry.record_classroom_question(session_id, transcript)
        responses: list[BehaviorResponse] = list(lifecycle_responses)
        classroom_wide = self._is_classroom_wide(transcript)
        if not target_students:
            if intent.speak:
                responses.extend(self._lower_hand_responses(
                    session_id,
                    self.registry.lower_raised_hands(session_id, reason="question_ended"),
                ))
            self._last_latency_ms["total_response"] = round(
                (time.perf_counter() - pipeline_started) * 1000, 2
            )
            return CommandResult(target="classroom" if responses else "none", responses=responses)
        responses.extend(self._lower_hand_responses(
            session_id,
            self.registry.lower_raised_hands(session_id, reason="student_selected"),
        ))
        memory_ms = 0.0
        tts_ms = 0.0
        for student in target_students:
            memory_started = time.perf_counter()
            memory = self.registry.get_conversation_memory(session_id, student.student_id)
            memory_ms += (time.perf_counter() - memory_started) * 1000
            history = memory["recent_turns"] or list(recent_turns or [])
            name_only_address = self._is_name_only_address(transcript, student)
            request = BehaviorRequest(
                session_id=session_id,
                persona_id=student.student_id,
                persona_name=student.name,
                persona_profile=student.personality,
                language=student.language,
                trainee_input=transcript,
                scene_state=scene_state,
                session_summary=self._session_summary(
                    session_id, student, session_summary, memory["summary"]
                ),
                recent_turns=history,
                knowledge_level=student.knowledge_level,
                grade_level=(
                    selected_grade_level
                    if selected_grade_level is not None
                    else self.registry.get_selected_grade_level(session_id)
                ),
                academic_behavior_enabled=(
                    student.knowledge_level == "INTERMEDIATE"
                    and "THINKING" in student.supported_actions
                    and "IDK" in student.supported_actions
                ),
                confidence=student.confidence,
                willingness=student.willingness,
                supported_actions=list(student.supported_actions),
                clarification_required=(
                    not name_only_address
                    and intent.action == "NONE"
                    and not intent.speak
                ),
                name_only_address=name_only_address,
                acknowledgment_required=not classroom_wide,
            )
            reply = self.behavior.generate_reply(request)
            if reply.action in {"THINKING", "IDK"} and reply.action not in student.supported_actions:
                reply = reply.model_copy(update={"action": "NONE"})
            thinking_response: BehaviorResponse | None = None
            if reply.action == "THINKING":
                thinking_response = reply.model_copy(update={
                    "dialogue": "",
                    "speak": False,
                    "audio_url": None,
                })
                # Preserve the supported IDK animation when the thinking
                # response already contains an uncertainty admission.
                final_action = (
                    "IDK"
                    if reply.emotion.casefold() in {"uncertain", "apologetic"}
                    and "IDK" in student.supported_actions
                    else "NONE"
                )
                reply = reply.model_copy(update={"action": final_action})
            elif (
                reply.action == "IDK"
                and reply.speak
                and reply.dialogue
                and "THINKING" in student.supported_actions
            ):
                # Direct academic/LLM IDK replies need the same preparation
                # event as uncertainty reached through the THINKING branch.
                thinking_response = reply.model_copy(update={
                    "dialogue": "",
                    "speak": False,
                    "audio_url": None,
                    "action": "THINKING",
                })
            behavior_latency = getattr(self.behavior, "latency_status", lambda: {})()
            for stage in ("llm_generation", "academic_selection", "ai_api", "ai_calls"):
                if behavior_latency.get(stage) is not None:
                    self._last_latency_ms[stage] = round(
                        (self._last_latency_ms.get(stage) or 0) + behavior_latency[stage], 2
                    )
            self.registry.update_session_state(
                session_id,
                student.student_id,
                emotion=reply.emotion,
                summary=reply.dialogue or None,
                data={"last_action": reply.action},
            )
            if reply.speak and reply.dialogue:
                tts_started = time.perf_counter()
                try:
                    reply = reply.model_copy(update={
                        "audio_url": self.tts.synthesize(
                            reply.dialogue,
                            f"{session_id}-{student.student_id}",
                            persona_id=student.student_id,
                            persona_name=student.name,
                            voice=student.voice,
                        )
                    })
                except Exception as error:
                    logger.exception(
                        "Student TTS failed session=%s persona=%s voice=%s error=%s",
                        session_id, student.student_id, student.voice, type(error).__name__,
                    )
                    reply = reply.model_copy(update={"audio_url": None})
                finally:
                    tts_ms += (time.perf_counter() - tts_started) * 1000
            else:
                reply = reply.model_copy(update={"audio_url": None})
            if thinking_response is not None:
                responses.append(thinking_response)
            responses.append(reply)
            if reply.speak or reply.dialogue:
                self.registry.record_conversation_turn(
                    session_id,
                    student.student_id,
                    transcript,
                    reply.dialogue,
                )
        self._last_latency_ms["conversation_memory_retrieval"] = round(memory_ms, 2)
        self._last_latency_ms["tts_generation"] = round(tts_ms, 2)
        self._last_latency_ms["total_response"] = round(
            (time.perf_counter() - pipeline_started) * 1000, 2
        )
        target = "classroom" if len(target_students) > 1 else target_students[0].student_id
        return CommandResult(target=target, responses=responses)

    def _handle_classroom_invitation(self, session_id: str, *, invitation_text: str = "") -> CommandResult:
        """Ask the behavior service for one independent decision per registered student."""
        question = self.registry.get_classroom_question(session_id) or invitation_text.strip()
        students = self.registry.list_students()
        if not question or not students:
            logger.info("Classroom invitation ignored because no latest question is available")
            return CommandResult(target="classroom", responses=[])
        round_number = self.registry.next_volunteer_round(session_id)
        decide = getattr(self.behavior, "decide_classroom_volunteers", None)
        if decide is None:
            from .behavior import BehaviorService
            decisions = BehaviorService._local_classroom_volunteer_decisions(
                students, round_number=round_number
            )
        else:
            decisions = decide(question, students, round_number=round_number)
        allowed_ids = {student.student_id for student in students}
        responses: list[BehaviorResponse] = []
        for student in students:
            action = decisions.get(student.student_id, "NONE") if isinstance(decisions, dict) else "NONE"
            if student.student_id not in allowed_ids or action not in {"RAISE_HAND", "NONE"}:
                logger.warning("Rejected invalid volunteer decision student=%s action=%s", student.student_id, action)
                continue
            if action == "RAISE_HAND":
                if self.registry.mark_hand_raised(
                    session_id, student.student_id, round_number=round_number
                ):
                    responses.append(BehaviorResponse(
                        persona_id=student.student_id,
                        dialogue="",
                        emotion="eager",
                        action="RAISE_HAND",
                        speak=False,
                        audio_url=None,
                        fallback=False,
                    ))
        latency = getattr(self.behavior, "latency_status", lambda: {})()
        for stage in ("classroom_volunteer_decision", "ai_api", "ai_calls"):
            if stage in latency:
                self._last_latency_ms[stage] = latency[stage]
        return CommandResult(target="classroom", responses=responses)

    def _lower_hand_responses(self, session_id: str, student_ids: list[str]) -> list[BehaviorResponse]:
        responses: list[BehaviorResponse] = []
        for student_id in student_ids:
            try:
                self.registry.get(student_id)
            except Exception:
                logger.warning("Skipping LOWER_HAND for unregistered student=%s", student_id)
                continue
            responses.append(BehaviorResponse(
                persona_id=student_id,
                dialogue="",
                emotion="neutral",
                action="LOWER_HAND",
                speak=False,
                audio_url=None,
                fallback=False,
            ))
        return responses

    def latency_status(self) -> dict:
        return dict(self._last_latency_ms)

    def expire_hand_raise_responses(self, session_id: str) -> list[BehaviorResponse]:
        """Build silent LOWER_HAND responses for hands that timed out."""
        return self._lower_hand_responses(
            session_id,
            self.registry.expire_hand_raises(session_id),
        )

    def _targets(self, transcript: str, default_persona_id: str) -> list[StudentRoute]:
        text = transcript.casefold()
        name_started = time.perf_counter()
        resolution = self.registry.resolve_with_status(text)
        self._last_latency_ms["student_name_detection"] = round(
            (time.perf_counter() - name_started) * 1000, 2
        )
        if resolution.ambiguous:
            logger.warning(
                "Student routing rejected ambiguous address transcript=%r aliases=%s",
                transcript,
                resolution.matched_aliases,
            )
            return []
        if resolution.profile is not None:
            if not self._is_direct_address(
                transcript, resolution.profile, resolution.matched_aliases
            ):
                logger.info(
                    "Student routing ignored incidental name mention transcript=%r student=%s",
                    transcript,
                    resolution.profile.student_id,
                )
                return []
            logger.info(
                "Student routing matched transcript=%r student=%s aliases=%s",
                transcript,
                resolution.profile.student_id,
                resolution.matched_aliases,
            )
            return [resolution.profile]

        if self._has_explicit_address(transcript):
            logger.warning(
                "Student routing rejected unrecognized explicit address transcript=%r",
                transcript,
            )
            return []

        classroom_wide = self._is_classroom_wide(transcript)
        if classroom_wide:
            return self.registry.list_students()
        logger.info("No explicit student address; ignoring individual transcript=%r", transcript)
        return []

    @staticmethod
    def _is_classroom_wide(transcript: str) -> bool:
        text = transcript.casefold()
        return MicrophoneCommandRouter._is_autonomous_hand_raise_request(text) or any(
            phrase in text for phrase in (
                "everyone", "all students", "buong klase", "classroom", "lahat",
            )
        )

    @staticmethod
    def _is_autonomous_hand_raise_request(text: str) -> bool:
        normalized = MicrophoneCommandRouter._normalized_address_text(text)
        phrases = (
            "who wants to answer", "who knows the answer", "who would like to answer",
            "would anyone like to answer", "any volunteers", "who wants to try",
            "who can answer", "sino ang gustong sumagot", "sino ang nakakaalam ng sagot",
            "sino ang may sagot", "sino ang sasagot", "may gustong sumagot",
            "sino ang gustong magbigay ng sagot", "who wants sumagot", "sino wants to answer",
            "anyone gustong sumagot",
        )
        return any(phrase in normalized for phrase in phrases)

    @staticmethod
    def _is_direct_raise_hand_command(text: str) -> bool:
        normalized = MicrophoneCommandRouter._normalized_address_text(text)
        phrases = (
            "raise your hand", "raise hand", "put your hand up", "taas kamay",
            "taas ang kamay", "itaas ang kamay", "itaas mo ang kamay",
            "itaas ang iyong kamay", "taas ang kamay mo",
        )
        return any(re.search(rf"\b{re.escape(phrase)}\b", normalized) for phrase in phrases)

    @staticmethod
    def _normalized_address_text(transcript: str) -> str:
        decomposed = unicodedata.normalize("NFKD", transcript.casefold())
        transcript_ascii = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return re.sub(
            r"\s+", " ", re.sub(r"[^\w]+", " ", transcript_ascii)
        ).strip()

    @classmethod
    def _is_direct_address(
        cls,
        transcript: str,
        student: StudentRoute,
        matched_aliases: tuple[str, ...] = (),
    ) -> bool:
        normalized = cls._normalized_address_text(transcript)
        punctuation_text = cls._normalized_address_with_punctuation(transcript)
        aliases = tuple(dict.fromkeys((*matched_aliases, *student.normalized_aliases())))
        for alias in sorted(aliases, key=len, reverse=True):
            if normalized == alias or normalized.startswith(f"{alias} "):
                return True
            if re.search(rf",\s*{re.escape(alias)}(?:[.!?]|$)", punctuation_text):
                return True
            if normalized.endswith(f" {alias}"):
                prefix = normalized[: -(len(alias) + 1)].split()
                if _DIRECT_ADDRESS_CUES.intersection(prefix):
                    return True
        return False

    @staticmethod
    def _normalized_address_with_punctuation(transcript: str) -> str:
        decomposed = unicodedata.normalize("NFKD", transcript.casefold())
        transcript_ascii = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return re.sub(r"\s+", " ", transcript_ascii).strip()

    @classmethod
    def _is_name_only_address(cls, transcript: str, student: StudentRoute) -> bool:
        normalized = cls._normalized_address_text(transcript)
        for alias in sorted(student.normalized_aliases(), key=len, reverse=True):
            normalized = re.sub(rf"\b{re.escape(alias)}\b", " ", normalized)
        return not re.sub(r"\s+", "", normalized)

    def _has_explicit_address(self, transcript: str) -> bool:
        """Detect likely name-addressed speech when registry matching failed."""
        segments = [segment.strip() for segment in re.split(r"[,!?]", transcript) if segment.strip()]
        candidates: list[str] = []
        if len(segments) >= 2:
            candidates.extend((segments[0], segments[-1]))
        else:
            tokens = re.findall(r"[\w']+", transcript.casefold())
            if tokens:
                candidates.append(tokens[0])
        for candidate in candidates:
            tokens = re.findall(r"[\w']+", candidate.casefold())
            if tokens and not any(token in _ADDRESS_STOP_WORDS for token in tokens):
                return True
        return False

    def _session_summary(
        self,
        session_id: str,
        student: StudentRoute,
        summary: str,
        conversation_summary: str = "",
    ) -> str:
        state = self.registry.get_session_state(session_id, student.student_id)
        parts = [summary, conversation_summary, f"state={state.current_state}", f"emotion={state.emotion}"]
        if state.summary:
            parts.append(f"memory={state.summary}")
        return "; ".join(part for part in parts if part)
