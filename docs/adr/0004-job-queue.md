# ADR-0004: Job queue library

Status: proposed

## Context

The worker runs long jobs: ingestion, model fitting, backtests, and Monte Carlo simulations. The API must enqueue a simulation and return immediately with a job id. Redis is already in the stack.

## Options

| Option | For | Against |
|---|---|---|
| Celery | Industry standard, well known in interviews, retries, scheduling (beat) | Heavier configuration |
| RQ | Very simple, Redis only | Sync only, fewer features |
| arq | Async, light | Smaller community, slower maintenance |

## Recommendation

Celery with Redis as broker, plus Celery beat for scheduled ingestion. Decide before M4.
