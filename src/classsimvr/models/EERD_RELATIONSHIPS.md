# ClassSim VR EERD relationship map

Source diagram: `ClassSim VR - EERD (colored).jpeg`

## ORM modules

- `base.py` — shared SQLAlchemy declarative base.
- `core.py` — `Trainee` and `Session`.
- `reference.py` — `Persona` and `Bti`.
- `interaction.py` — `Consent`, `Recording`, `Report`, `Turn`, `SessionPersona`, and `SessionBti`.
- `scoring.py` — `IndicatorScore`, `ObservedScore`, `NotObserved`, and `Evidence`.
- `database.py` — PostgreSQL engine, session factory, and `get_session()` dependency.

## Entities

- **TRAINEE** — core entity; `trainee_id` primary key.
- **SESSION** — core entity; `session_id` primary key and `trainee_id` foreign key.
- **PERSONA** — reference entity; `persona_id` primary key.
- **BTI** — reference entity; `bti_id` primary key.
- **CONSENT**, **RECORDING**, **REPORT** — weak entities identified by `SESSION`.
- **TURN** — weak interaction entity with foreign keys to `SESSION` and `PERSONA`.
- **SESSION_PERSONA**, **SESSION_BTI** — many-to-many junction entities with composite primary keys.
- **INDICATOR_SCORE** — parent score entity with foreign keys to `REPORT` and `BTI`.
- **OBSERVED_SCORE**, **NOT_OBSERVED** — disjoint joined-table subtypes of `INDICATOR_SCORE`.
- **EVIDENCE** — weak evidence entity attached to `OBSERVED_SCORE`.

## Relationships

| Relationship | Cardinality | ORM mapping |
|---|---:|---|
| TRAINEE runs SESSION | 1 → 0..* | `Session.trainee_id` → `Trainee.trainee_id` |
| SESSION requires CONSENT | 1 → 0..1 | `Consent.session_id` → `Session.session_id` |
| SESSION captures RECORDING | 1 → 0..1 | `Recording.session_id` → `Session.session_id` |
| SESSION yields REPORT | 1 → 0..1 | `Report.session_id` → `Session.session_id` |
| SESSION logs TURN | 1 → 0..* | `Turn.session_id` → `Session.session_id` |
| PERSONA speaks in TURN | 1 → 0..* | `Turn.persona_id` → `Persona.persona_id` |
| SESSION features SESSION_PERSONA | 1 → 0..* | `SessionPersona.session_id` → `Session.session_id` |
| PERSONA cast in SESSION_PERSONA | 1 → 0..* | `SessionPersona.persona_id` → `Persona.persona_id` |
| SESSION selects SESSION_BTI | 1 → 0..* | `SessionBti.session_id` → `Session.session_id` |
| BTI chosen in SESSION_BTI | 1 → 0..* | `SessionBti.bti_id` → `Bti.bti_id` |
| REPORT contains INDICATOR_SCORE | 1 → 1..* | `IndicatorScore.report_session_id` → `Report.session_id` |
| BTI measures INDICATOR_SCORE | 1 → 0..* | `IndicatorScore.bti_id` → `Bti.bti_id` |
| INDICATOR_SCORE ISA OBSERVED_SCORE | disjoint | `ObservedScore.score_id` → `IndicatorScore.score_id` |
| INDICATOR_SCORE ISA NOT_OBSERVED | disjoint | `NotObserved.score_id` → `IndicatorScore.score_id` |
| OBSERVED_SCORE backed by EVIDENCE | 1 → 0..* | `Evidence.score_id` → `ObservedScore.score_id` |
