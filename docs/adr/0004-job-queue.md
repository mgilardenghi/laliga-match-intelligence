# ADR-0004: Job queue library

Status: Accepted (2026-11-16, approved by the user)

## Context

The worker runs long jobs: ingestion, model fitting, backtests, and Monte Carlo simulations. The API must enqueue a simulation and return immediately with a job id. Redis is already in the stack.

## Options

| Option | For | Against |
|---|---|---|
| Celery | Industry standard, well known in interviews, retries, scheduling (beat) | Heavier configuration |
| RQ | Very simple, Redis only | Sync only, fewer features |
| arq | Async, light | Smaller community, slower maintenance |

## Decision

Celery with Redis as the broker, plus Celery beat for the scheduled ingestion. Celery is the industry standard, it carries retries and a scheduler in one library, and its configuration is the main cost of the choice.

The rejected alternatives were RQ, which has no built-in cron and would need a second scheduler, and arq, whose async model contradicts the synchronous access of ADR-0005. A PostgreSQL `SKIP LOCKED` queue would have been the smallest option, but it leaves Redis unused and departs from this decision; it stays the fallback if Celery is rejected later.

## Amendment (M4, task T6)

M4 fixes how Celery is configured and what the stack guarantees. The decision above stands unchanged; this section records the rules a change must not break.

1. **No result backend.** Job state and results live in PostgreSQL. A task is a pointer to a row: the message carries a run id, and every input is read from the database.
2. **Enqueue only after the row commits.** If `send_task` fails, the row is marked `failed` and the API answers 503.
3. **Tasks are idempotent.** The claim is:

   ```sql
   UPDATE ... SET status='running' WHERE id=:id AND (status='queued' OR (status='running' AND started_at < now() - interval '30 minutes'))
   ```

   A rowcount of 0 means skip: another executor owns the run or has already finished it.
4. **Settings.** `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, `task_ignore_result=True`, `timezone='Europe/Madrid'`, the JSON serializer, and `broker_transport_options={'visibility_timeout': 3600}`. Worker concurrency is 1, because simulations are CPU-bound on one laptop.
5. **Recovery sweep on `worker_ready`.** The worker re-enqueues `queued` rows older than 1 minute and `running` rows whose `started_at` is older than 30 minutes, so the claim's stale branch picks them up. `create_run` also re-enqueues a stale active row instead of returning a dead run, with the same two windows as the sweep: 1 minute for a `queued` row (`QUEUED_STALE_AFTER`) and 30 minutes for a `running` row (`STALE_AFTER`), both in `lmi_core.simulation.service`. Real-world recovery after a crash is therefore bounded by the 30-minute stale window, or by the visibility timeout if no worker restarts.
6. **Beat runs in the Compose profile `scheduler`, off by default.** Its daily cycle downloads data, and the default stack must stay offline.

## Consequences

- The queue holds only a run id, so a lost message leaves a row that the sweep can repair.
- No result backend means the API reads the outcome from PostgreSQL.
- Celery is the job-queue library of ADR-0003's "Cache and job broker: Redis" row, declared as `celery[redis]` by both the API and the worker packages.
- A simulation is CPU-bound, so one worker with concurrency 1 is the intended deployment.
