from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .persona_contracts import AssignedPersonaResponse


# IDs and grade ranges match Unreal's ElementaryCurriculum catalog.
ELEMENTARY_SUBJECTS = {
    "Language": (1, 1, "Language"),
    "ReadingAndLiteracy": (1, 1, "Reading and Literacy"),
    "English": (2, 6, "English"),
    "Filipino": (2, 6, "Filipino"),
    "Mathematics": (1, 6, "Mathematics"),
    "Science": (3, 6, "Science"),
    "Makabansa": (1, 3, "Makabansa"),
    "AralingPanlipunan": (4, 6, "Araling Panlipunan"),
    "GMRC": (1, 6, "Good Manners and Right Conduct"),
    "EPP": (4, 6, "Edukasyong Pantahanan at Pangkabuhayan"),
    "MusicAndArts": (4, 6, "Music and Arts"),
    "PhysicalEducationAndHealth": (4, 6, "Physical Education and Health"),
}
ELEMENTARY_GRADES = {f"Grade {n}": n for n in range(1, 7)}


@dataclass(frozen=True)
class ClassroomContext:
    grade: str
    subject: str
    session_mode: Literal["practice", "assessment"]

    @classmethod
    def from_selection(cls, grade: str | None, subject: str | None, mode: str) -> "ClassroomContext":
        number = ELEMENTARY_GRADES.get(grade)
        details = ELEMENTARY_SUBJECTS.get(subject)
        if not number or not details or not details[0] <= number <= details[1]:
            raise ValueError("subject is not available for the selected elementary grade")
        if mode not in ("practice", "assessment"):
            raise ValueError("invalid classroom session mode")
        return cls(grade, subject, mode)

    @property
    def subject_label(self) -> str:
        return ELEMENTARY_SUBJECTS[self.subject][2]

    def prompt_context(self) -> dict[str, str]:
        return {"grade": self.grade, "subject": self.subject_label, "session_mode": self.session_mode}


MIN_CLASS_SIZE = 15
MAX_CLASS_SIZE = 30

SUBJECTS_BY_GRADE: dict[str, tuple[str, ...]] = {
    "Elementary": ("English", "Mathematics", "Science", "Araling Panlipunan"),
    "High School": ("English", "Mathematics", "Science", "Filipino"),
    "Senior High": ("English", "General Mathematics", "Earth and Life Science", "Practical Research"),
    "College": ("Communication", "Education", "Information Technology", "Research Methods"),
}

TOPICS_BY_SUBJECT: dict[str, tuple[str, ...]] = {
    "English": ("Reading comprehension", "Parts of speech", "Oral communication", "Writing a clear paragraph"),
    "Mathematics": ("Fractions and decimals", "Linear equations", "Geometry", "Problem solving"),
    "Science": ("The water cycle", "Matter and its properties", "Ecosystems", "Scientific investigation"),
    "Araling Panlipunan": ("Philippine history", "Culture and identity", "Civic responsibility", "Geography of the Philippines"),
    "Filipino": ("Pag-unawa sa binasa", "Wastong gamit ng wika", "Pagsasalaysay", "Panitikan"),
    "General Mathematics": ("Functions", "Financial mathematics", "Statistics", "Logic and reasoning"),
    "Earth and Life Science": ("Earth systems", "Biodiversity", "Climate and weather", "Life processes"),
    "Practical Research": ("Research problem", "Literature review", "Research design", "Data interpretation"),
    "Communication": ("Interpersonal communication", "Public speaking", "Media literacy", "Professional writing"),
    "Education": ("Lesson planning", "Learner-centered teaching", "Classroom management", "Assessment strategies"),
    "Information Technology": ("Programming fundamentals", "Data and information", "Web technologies", "Cybersecurity basics"),
    "Research Methods": ("Research questions", "Qualitative methods", "Quantitative methods", "Research ethics"),
}


class StudentGenerationRequest(BaseModel):
    class_size: int = Field(ge=MIN_CLASS_SIZE, le=MAX_CLASS_SIZE)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class SessionCreate(BaseModel):
    trainee_id: int = Field(gt=0)
    grade: str | None = Field(default=None, max_length=50)
    subject: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=200)
    session_mode: Literal["practice", "assessment"] = "practice"
    competency: str | None = Field(default=None, max_length=4_000)
    lesson_plan: str | None = Field(default=None, max_length=12_000)
    answer_key: str | None = Field(default=None, max_length=12_000)
    class_size: int | None = Field(default=None, ge=MIN_CLASS_SIZE, le=MAX_CLASS_SIZE)
    difficulty: str | None = Field(default=None, max_length=50)

    start_immediately: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def validate_teaching_setup(self) -> "SessionCreate":
        if self.start_immediately or (self.subject and self.grade in ELEMENTARY_GRADES):
            ClassroomContext.from_selection(self.grade, self.subject, self.session_mode)
            if self.topic and self.topic not in TOPICS_BY_SUBJECT.get(self.subject, ()):
                raise ValueError("topic is not available for the selected subject")
            return self

        if self.topic and not self.subject:
            raise ValueError("topic requires a subject")
        if self.subject and not self.grade:
            raise ValueError("subject requires a grade level")
        if not self.subject:
            return self

        allowed_subjects = SUBJECTS_BY_GRADE.get(self.grade or "")
        if allowed_subjects is None:
            raise ValueError("grade must be Elementary, High School, Senior High, or College")
        if self.subject not in allowed_subjects:
            raise ValueError("subject is not available for the selected grade level")
        if self.topic and self.topic not in TOPICS_BY_SUBJECT.get(self.subject, ()):
            raise ValueError("topic is not available for the selected subject")
        return self


class SessionResponse(BaseModel):
    session_id: int
    trainee_id: int
    grade: str | None
    subject: str | None
    topic: str | None
    session_mode: Literal["practice", "assessment"]
    competency: str | None
    lesson_plan: str | None
    answer_key: str | None
    class_size: int | None
    difficulty: str | None
    status: str
    created_at: datetime


class TraineeHistoryResponse(BaseModel):
    session_id: int
    trainee_id: int
    grade: str | None
    subject: str | None
    topic: str | None
    session_mode: Literal["practice", "assessment"]
    competency: str | None
    class_size: int | None
    difficulty: str | None
    status: str
    assessment_status: str
    assessment_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    report_available: bool
    summary: str | None
    next_focus: str | None


class GeneratedClassResponse(BaseModel):
    session_id: int
    class_size: int
    seed: int
    students: list[AssignedPersonaResponse]


class SessionLifecycleResponse(BaseModel):
    session_id: int
    trainee_id: int
    status: str
    class_size: int | None
    started_at: object | None
    paused_at: object | None
    completed_at: object | None
