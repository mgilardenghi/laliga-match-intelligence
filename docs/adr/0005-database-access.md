# ADR-0005: Database access

Status: accepted (2026-11-13)

## Context

The API and the worker both use PostgreSQL. The worker writes in batches: it validates a whole file first, then writes one match at a time, with a run record around the file. The API serves short read queries. The schema grows with every milestone, and every environment starts from an empty database that still needs the same reference data (canonical teams, the alias map, the seasons).

Three questions have to be answered once: how to talk to the database, how to model the constrained columns, and how a schema and the reference data reach a fresh database.

## Decision

### Synchronous SQLAlchemy 2 with psycopg 3

Both services use synchronous SQLAlchemy 2 with the psycopg 3 driver.

The worker is a batch job: it downloads files, validates them and writes row sets. Synchronous code keeps that readable and matches the data libraries in the stack. The API is a FastAPI service that serves synchronous endpoints from a thread pool, which is enough for the expected traffic on localhost. An async driver would add session-per-request plumbing, async test fixtures and a second dialect path, with no measured need behind it. If a request shape that needs async database I/O appears, this decision is revisited with measurements.

### Text columns with CHECK constraints instead of native enums

Categorical columns (`status`, `source`, `bookmaker`) are TEXT with a CHECK constraint that lists the allowed values. A PostgreSQL enum type cannot be extended or narrowed inside a transaction, needs its own migration dance to change a label, and cannot be dropped while a column uses it. A CHECK constraint gives the same guarantee and changes with one line in a migration.

### Alembic for the schema, a one-shot service for applying it

Alembic owns the schema and is configured programmatically: `lmi_core.db.alembic_support.alembic_config` builds an `alembic.config.Config` whose `script_location` is the packaged `lmi_core/db/migrations` directory and whose URL comes from the settings. There is no `alembic.ini` in the working directory, so the same code runs from a checkout and from an image.

The schema is applied by the one-shot `migrate` Compose service: `python -m lmi_core.db migrate` runs `alembic upgrade head` and then the seed. The API and the worker declare `depends_on: migrate: condition: service_completed_successfully`. No process starts against an unmigrated database, and no process migrates on startup.

### Idempotent seed from a packaged file

Reference data that no source provides is a CSV packaged in `lmi_core/data/`: canonical team names, the alias map from each source's team names to a canonical team, and the season codes with the football-data directory code.

The seed inserts what is missing and is safe to run on every start. An existing `(source, alias)` pair is never remapped: if the file maps a pair to a different team than the database holds, the seed raises and the `migrate` service fails. A silent remap would rewrite the identity of historical matches. Names are normalized (Unicode NFC, collapsed whitespace) on both sides and matched exactly; there is no fuzzy matching, because a near miss is a data error that has to be seen.

### Connection URL built from parts

The services read `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, and `lmi_core.config.sqlalchemy_url` builds the URL with `sqlalchemy.engine.URL.create`. `DATABASE_URL` remains a full override for a deployment that passes one string; when it is set, the parts are ignored.

Building from parts means a password containing `@`, `:`, `/`, `?`, `#` or `%` needs no URL quoting, and Compose never interpolates a credential into a URL.

## Consequences

- One database access path for the API and the worker; no async engine to maintain.
- Changing an allowed value is a CHECK constraint edit in a migration, not an enum type change.
- A fresh database is one command (`make up`, or `docker compose run --rm migrate`), and running it again changes nothing.
- A wrong team-name mapping fails the migration job instead of producing wrong data later.
- The API and the worker cannot start until the migration job has exited 0, so a broken migration blocks the whole stack loudly instead of half-working.
- The seed owns the alias table. A new source team name is a data change to the CSV, reviewed like code.
