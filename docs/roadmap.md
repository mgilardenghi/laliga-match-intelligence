# Roadmap

Milestones M0 to M6 and their acceptance criteria live in the [MVP specification](spec/mvp.md). This page records the phases after the MVP and decisions taken early so they are not lost.

## After the MVP

| Phase | Content |
| --- | --- |
| Players (Phase 2) | Player-level analysis for the focus team. API-Football's free plan covers only seasons 2022–2024 plus the current squad (verified live 2026-11-12); the current season needs a paid plan. Its terms grant no publication license, so data is fetched at runtime and never committed. |
| RAG and agent | An LLM agent that writes grounded match reports from database facts, with evals that check every number against the database, token-cost tracking per report, and pgvector in the same PostgreSQL. |
| Platform | Kubernetes (kind or k3d) with Helm and Terraform, then a minimal GCP deployment (Cloud Run, Cloud SQL). |

## Early decisions (2026-11-13)

- **Durable agent execution, built in Python.** For the RAG and agent phase, take the ideas of Earendil's pi-durable without the dependency (it is experimental TypeScript whose API changes without notice): agent steps checkpointed in a PostgreSQL table so a run resumes after a crash; idempotency keys on API requests and Celery tasks; tools classified as safe or unsafe to replay; an approval gate before any action with side effects.
- **No AWS emulator in this project.** GCP stays the primary cloud and Docker Compose the local environment. AWS work belongs to a separate portfolio project.
- **modern-web-guidance for the dashboard milestone (M5).** Install the GoogleChrome/modern-web-guidance agent skill at project level with a pinned version, only when M5 starts, so its broad triggers do not fire during backend work. It covers web-platform correctness (modern CSS, performance, forms, accessibility), not visual design; the frontend-design skill covers the look.
