from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

OBSERVED_SCORE_MIN = 1
OBSERVED_SCORE_MAX = 4


class BtiRubricItem(BaseModel):
    bti_code: str
    domain: str
    strand: str
    title: str
    indicator: str


class ObservedAssessmentItem(BaseModel):
    bti_code: str = Field(min_length=1, max_length=20)
    score: int = Field(ge=OBSERVED_SCORE_MIN, le=OBSERVED_SCORE_MAX)
    note: str | None = Field(default=None, max_length=4_000)
    t_start_s: float | None = Field(default=None, ge=0)
    t_end_s: float | None = Field(default=None, ge=0)


class AssessmentJobResponse(BaseModel):
    job_id: int
    session_id: int
    status: str
    provider: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AssessmentEvidence(BaseModel):
    t_start_s: float = Field(ge=0)
    t_end_s: float = Field(ge=0)
    note: str = Field(min_length=1, max_length=4_000)


class AssessmentResultIndicator(BaseModel):
    bti_code: str = Field(min_length=1, max_length=20)
    observed: bool
    score: int | None = Field(default=None, ge=OBSERVED_SCORE_MIN, le=OBSERVED_SCORE_MAX)
    evidence: list[AssessmentEvidence] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_observation(self) -> "AssessmentResultIndicator":
        if self.observed and self.score is None:
            raise ValueError("observed indicators require a score")
        if not self.observed and (self.score is not None or self.evidence):
            raise ValueError("not-observed indicators cannot contain score or evidence")
        return self


class AssessmentResult(BaseModel):
    summary: str = Field(min_length=1, max_length=4_000)
    next_focus: str | None = Field(default=None, max_length=2_000)
    model_assess: str | None = Field(default=None, max_length=4_000)
    indicators: list[AssessmentResultIndicator] = Field(min_length=1, max_length=10)


class AssessmentResultResponse(BaseModel):
    job_id: int
    session_id: int
    status: str
    report_session_id: int


class ReportEvidenceResponse(BaseModel):
    t_start_s: float | None
    t_end_s: float | None
    note: str | None


class ReportIndicatorResponse(BaseModel):
    bti_code: str
    title: str | None = None
    observed: bool
    score: int | None
    evidence: list[ReportEvidenceResponse]


class ReportResponse(BaseModel):
    session_id: int
    summary: str | None
    next_focus: str | None
    model_assess: str | None
    indicators: list[ReportIndicatorResponse]
    transcript_feedback: dict | None = None


def _rubric(
    bti_code: str, domain: str, strand: str, title: str, indicator: str
) -> BtiRubricItem:
    return BtiRubricItem(
        bti_code=bti_code,
        domain=domain,
        strand=strand,
        title=title,
        indicator=indicator,
    )


_RUBRIC = (
    _rubric("1.1.1", "D1", "1.1", "Content delivery and accuracy", "Content knowledge and its application within and across curriculum teaching areas"),
    _rubric("1.5.1", "D1", "1.5", "Questioning and higher-order thinking", "Teaching strategies that develop critical and creative thinking and other HOTS"),
    _rubric("1.6.1", "D1", "1.6", "Language use and code-switching", "Use of Mother Tongue, Filipino, and English to facilitate teaching and learning"),
    _rubric("1.7.1", "D1", "1.7", "Verbal and non-verbal communication", "Range of verbal and non-verbal classroom communication strategies"),
    _rubric("2.2.1", "D2", "2.2", "Fair, respectful, and caring climate", "Learning environments that promote fairness, respect, and care"),
    _rubric("2.3.1", "D2", "2.3", "Classroom structure and activity management", "Managing classroom structure that engages learners in meaningful activities"),
    _rubric("2.4.1", "D2", "2.4", "Learner participation and engagement", "Supportive learning environments that nurture and inspire participation"),
    _rubric("2.5.1", "D2", "2.5", "Promotion of purposive learning", "Environments that motivate learners to take responsibility for their own learning"),
    _rubric("2.6.1", "D2", "2.6", "Management of learner behavior", "Positive and non-violent discipline in managing learner behavior"),
    _rubric("3.1.1", "D3", "3.1", "Responsiveness to learner diversity", "Differentiated teaching suited to learners' gender, needs, strengths, interests, and experiences"),
)


def list_bti_rubric() -> list[BtiRubricItem]:
    return list(_RUBRIC)
