# ClassSim VR SQLAlchemy EERD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all EERD entities and relationships as PostgreSQL-ready SQLAlchemy ORM models with a database session layer.

**Architecture:** Use one shared declarative base in `models/base.py`, split entity classes across focused model modules, and re-export them from `models/__init__.py`. Provide lazy PostgreSQL engine/session configuration in `database.py`; no connection occurs on import.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.x typed ORM, psycopg 3, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-sqlalchemy-eerd-design.md`

## Global Constraints

- PostgreSQL is the production database target.
- `DATABASE_URL` configures the database; default to a local PostgreSQL URL only when the caller does not provide one.
- Importing the model package must not connect to a database.
- Do not add CRUD routes or modify LLM services.

## Review Focus

- Composite junction keys must reject duplicate session/entity pairs; test PK columns.
- Weak entities must use their parent session ID as both PK and FK; test constraints.
- Score subtypes must share the parent score identity and use a discriminator; test mapper configuration.
- The database module must be importable without a running PostgreSQL server; test engine creation without connecting.
- Relationship collections must point to the expected target classes; test mapper relationship names.

### Task 1: Shared ORM base and database layer

**Files:**
- Create: `src/classsimvr/models/base.py`
- Create: `src/classsimvr/database.py`
- Create: `tests/test_database_layer.py`

**Interfaces:**
- Produces `Base`, `DATABASE_URL`, `engine`, `SessionLocal`, and `get_session()`.

- [ ] Write failing tests for declarative metadata availability and non-connecting engine/session construction.
- [ ] Run `pytest tests/test_database_layer.py -v` and observe failure because modules do not exist.
- [ ] Implement typed declarative base and lazy engine/session factory.
- [ ] Run the focused tests and verify they pass.
- [ ] Run the full test suite.

### Task 2: Core and reference entities

**Files:**
- Create: `src/classsimvr/models/core.py`
- Create: `src/classsimvr/models/reference.py`
- Modify: `src/classsimvr/models/__init__.py`
- Create: `tests/test_core_models.py`

**Interfaces:**
- Produces `Trainee`, `Session`, `Persona`, and `Bti` models with relationship collections and foreign keys.

- [ ] Write failing tests for table names, primary keys, `SESSION.trainee_id`, and core relationship targets.
- [ ] Run the focused tests and observe failure.
- [ ] Implement the core/reference models and relationships.
- [ ] Run the focused tests and verify they pass.
- [ ] Run the full test suite.

### Task 3: Weak, junction, and turn entities

**Files:**
- Create: `src/classsimvr/models/interaction.py`
- Create: `tests/test_interaction_models.py`

**Interfaces:**
- Consumes `Base`, `Session`, `Persona`, and `Bti`.
- Produces `Consent`, `Recording`, `Report`, `Turn`, `SessionPersona`, and `SessionBti`.

- [ ] Write failing tests for weak-entity PK/FK constraints, composite junction keys, and relationship targets.
- [ ] Run the focused tests and observe failure.
- [ ] Implement the weak/junction/turn models.
- [ ] Run the focused tests and verify they pass.
- [ ] Run the full test suite.

### Task 4: Score hierarchy and evidence

**Files:**
- Create: `src/classsimvr/models/scoring.py`
- Modify: `src/classsimvr/models/__init__.py`
- Create: `tests/test_scoring_models.py`

**Interfaces:**
- Consumes `Base`, `Report`, and `Bti`.
- Produces `IndicatorScore`, `ObservedScore`, `NotObserved`, and `Evidence`.

- [ ] Write failing tests for score foreign keys, joined-table inheritance, discriminator values, and evidence targeting observed scores.
- [ ] Run the focused tests and observe failure.
- [ ] Implement score and evidence models.
- [ ] Run the focused tests and verify they pass.
- [ ] Run the full test suite.

### Task 5: Package integration and final verification

**Files:**
- Modify: `src/classsimvr/models/__init__.py`
- Modify: `src/classsimvr/models/EERD_RELATIONSHIPS.md`
- Create: `tests/test_model_imports.py`

- [ ] Add complete exports and update relationship documentation with the implemented module names.
- [ ] Write failing import/integration tests for all model classes.
- [ ] Run the focused tests and observe failure.
- [ ] Implement package integration.
- [ ] Run `pytest -q` and verify the complete suite passes.
- [ ] Inspect `git diff` and verify only intended files changed.
