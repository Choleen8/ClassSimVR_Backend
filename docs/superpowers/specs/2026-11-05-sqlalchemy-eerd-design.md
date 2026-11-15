# ClassSim VR SQLAlchemy EERD Design

## Intent

Implement every entity from the ClassSim VR Enhanced ERD as SQLAlchemy 2.x ORM models, with explicit foreign keys, composite junction keys, weak-entity identifiers, and the disjoint score subtype hierarchy. The database layer will be configured for PostgreSQL but will not create or connect to a database until the application uses it.

## Goals

- Represent all EERD entities in Python ORM models.
- Preserve the EERD primary-key, foreign-key, identifying, junction, and ISA relationships.
- Expose a PostgreSQL-ready engine/session factory using `DATABASE_URL`.
- Keep imports and schema inspection usable without a live database.
- Add tests for table names, keys, foreign keys, relationship collections, and subtype identity.

## Non-goals

- Running migrations or creating tables automatically.
- Connecting to a live PostgreSQL instance.
- Adding CRUD routes or changing the existing LLM services.

## Approach

The chosen approach is SQLAlchemy declarative ORM because the project already depends on SQLAlchemy and `psycopg[binary]`, and the user plans to use PostgreSQL. Models will use a shared `DeclarativeBase`, typed `Mapped` attributes, explicit `relationship()` declarations, and PostgreSQL-compatible column types. The database module will provide an engine factory and sessionmaker without opening a connection at import time.

## Model decisions

- `SESSION_PERSONA` and `SESSION_BTI` use composite primary keys.
- `CONSENT`, `RECORDING`, and `REPORT` use `session_id` as both primary key and foreign key to `SESSION`.
- `TURN` references both `SESSION` and `PERSONA`.
- `INDICATOR_SCORE` is the parent of `OBSERVED_SCORE` and `NOT_OBSERVED` using joined-table inheritance with a discriminator; the two subtypes are disjoint by application/domain convention.
- `EVIDENCE.score_id` references `OBSERVED_SCORE.score_id`, so evidence cannot attach to `NOT_OBSERVED` through the ORM schema.
- PostgreSQL is the production target; tests use SQLAlchemy metadata inspection and SQLite only where an in-memory execution check is useful.

## Constraints

- Keep the existing model folder: `src/classsimvr/models`.
- Keep the image and relationship documentation already placed there.
- Do not require a database server for import-time behavior or tests.
