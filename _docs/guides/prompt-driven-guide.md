# Classora — Prompt-Driven Development Guide

**Workflow:** One developer drives work with explicit prompts to a coding assistant. **No GitHub Issues backlog is required.**

> This is an implementation guide, not the root `README.md`. The approved product scope lives in `_docs/spec.md`. Architectural constraints live in `_docs/architecture.md`. Never replace approved decisions with suggestions from the agent without recording an explicit decision.

## 1. Shared Classora baseline

Classora manages one tutoring and exam-preparation center: students, guardians, tutors, individual and group lessons, courses, scheduling, academic performance, files, billing, payroll, expenses, and reporting. The interface is Spanish/English, currency is MXN, and users have strictly scoped role/record permissions.

**Stack:** React + Vite + TypeScript, Tailwind/shadcn/ui, TanStack Query/Zustand, React Hook Form/Zod, FullCalendar, FastAPI + async SQLAlchemy/asyncpg/Alembic, self-hosted PostgreSQL + PgBouncer, Procrastinate (including periodic jobs), S3 uploads, Docker Compose, EC2, Terraform, GitHub Actions + ECR + SSM, Caddy + CloudFront, and OpenTelemetry/Grafana/Loki/Prometheus/Tempo. **No Redis, Celery, RDS, or separate message broker.** DEV and PROD each have an independent EC2/PostgreSQL deployment; monitoring uses a separate instance.

Use a single repository structure in *both* guides:

```text
classora/
├── README.md                    # Human-facing entry point: short, current
├── AGENTS.md                    # Agent rules, including README review gate
├── CLAUDE.md                    # @AGENTS.md
├── apps/
│   ├── web/                     # React, TypeScript, Vite
│   └── api/                     # FastAPI, Procrastinate worker, Alembic, tests
├── packages/api-client/         # Generated OpenAPI TypeScript client
├── infrastructure/
│   ├── terraform/
│   ├── docker/
│   ├── observability/
│   └── scripts/
├── _docs/
│   ├── spec.md
│   ├── architecture.md
│   ├── data-model.md
│   ├── api.md
│   ├── design-system.md
│   ├── testing-guidelines.md
│   ├── process.md              # Prompt-driven working agreement
│   └── adr/
├── e2e/
├── .github/workflows/
├── compose.yaml
└── Makefile
```

Do not create a competing `docs/`, `frontend/`, or `backend/` tree.

## 2. Two distinct READMEs

- **Root `README.md` is for humans** deciding what the project is and how to run/contribute to it. It is not a prompt archive, sprint journal, issue log, full spec, or playbook.
- **This guide** explains the development *process* in detail, and belongs under `_docs/` or in planning materials, not as root `README.md`.

### Root README required sections (only as relevant)

1. **Classora:** one-paragraph purpose and product status.
2. **Features:** a few bullets describing implemented capabilities; planned items identified clearly as planned.
3. **Tech stack:** compact summary.
4. **Quick start:** commands that have actually been implemented and verified; prerequisites and configuration.
5. **Development commands:** tested commands for running, testing, and linting.
6. **Documentation:** links to `_docs/spec.md`, `_docs/architecture.md`, this guide, relevant contribution instructions.
7. **Deployment/status:** brief DEV/PROD summary only after those environments exist.

Avoid speculative URLs, unsupported instructions, changelogs, deep API descriptions, sensitive endpoints, secrets, and lengthy architecture diagrams in root README. Link to detailed docs instead.

### Mandatory README gate after **every prompt**

Before ending *every coding prompt*, inspect the changes and decide if the root README needs changes. Update it **in the same work unit** whenever user-visible features, working setup commands, environment requirements, deployment status, tech stack, or documentation links changed. If no reader-facing information changed, leave it untouched and explicitly report **`README reviewed: no update necessary`**. If changed, report **`README updated: <sections>`**. Never create empty/noisy README commits or append session summaries to it. Validate all commands and links claimed to work.

Put this rule in `AGENTS.md` so it applies even when the user forgets to repeat it.

## 3. Project bootstrap: one prompt at a time

Start with the checked-in `_docs/spec.md` and create `_docs/architecture.md` from *approved* decisions. Then establish the repo and working build in a small slice.

**Prompt 0 — lock requirements and architecture (no code):**

```text
Read _docs/spec.md. Write or reconcile _docs/architecture.md using only the
approved Classora decisions. Identify unresolved implementation details separately.
Do not write application code, do not change product scope, and do not introduce
Redis, Celery, RDS, or a multi-tenant model.
Review README.md and update it if its claims or documentation links changed.
Report either 'README updated: ...' or 'README reviewed: no update necessary'.
```

**Prompt 1 — bootstrap a runnable skeleton:**

```text
Read AGENTS.md, _docs/spec.md and _docs/architecture.md.
Implement ONLY a monorepo skeleton: apps/web (React/Vite/TS), apps/api
(FastAPI), PostgreSQL via Compose, initial Alembic migration,
health/readiness endpoints, minimal backend and frontend passing tests,
Makefile commands for dev/test/lint, and package tooling (uv, pnpm).
No business features, cloud deployment, Redis, or Celery.
Run what you can; report exact commands/results. Commit cohesive changes.
Mandatory: inspect root README.md, update relevant human-facing sections
if necessary, verify every claimed command/link, and report the README verdict.
```

**Prompt 2 — frontend prototype with centralized mock services:**

```text
Build the Classora app shell and role-adaptive navigation using _docs/spec.md.
Implement representative read-only demo flows for owner/admin, tutor, student
and guardian; use a typed, centralized service interface with mock data.
Keep mocked data and auth clearly labeled. Cover bilingual responsive UI,
accessibility, and empty/loading/error states. Add UI tests. Do not invent
backend endpoints or claim production readiness.
Run tests, update applicable design docs, and perform the root README gate.
```

**Prompt 3 — API contract before persistence:**

```text
From _docs/spec.md and the frontend service interface, design the REST API
in _docs/api.md (routes, permissions, request/response schemas, problem details).
Use /api/v1 and OpenAPI generated by FastAPI as the authoritative executable
contract. Generate/update packages/api-client. Implement a minimal API slice
with contract tests. Avoid bearer-token auth as the final solution: use the
approved PostgreSQL-backed server sessions and secure cookies.
Perform the root README gate and report the result.
```

**Prompt 4 — production database foundations:**

```text
Implement PostgreSQL-backed identity, users, roles, and guardian/student links
from _docs/spec.md. Before coding, document tables, UUID keys, unique/check/FK
constraints, indexes, soft-delete/history strategy, sessions, and transactional
boundaries in _docs/data-model.md. Use SQLAlchemy 2 async + asyncpg + Alembic;
no SQLite migration path is needed. Add real PostgreSQL integration tests with
Testcontainers, authorization/ownership tests and safe migrations.
Perform the root README gate and report the result.
```

## 4. Continue by vertical slices, not disconnected layers

Each following prompt should implement **one end-to-end user outcome**: data model, API, UI, security, and tests. Suggested sequence:

1. Login, invitations, role access, guardian/student linking.
2. Tutor profiles, qualifications, availability, time off, rooms.
3. Subject catalog, bookings, RRULE recurrence, conflict checks, approvals.
4. Course templates, cohorts, modules, enrollments, capacity rules.
5. Attendance, session closeout, academic plans, evaluations, assignments.
6. Charges, prepaid credits, family balances, payments and reversals.
7. Tutor compensation, approved hours, payroll periods and adjustments.
8. Expenses, reports, imports/exports, documents, in-app notifications.
9. Operational hardening and production release.

**Reusable feature prompt:**

```text
Implement one vertical slice: <specific Classora outcome>.
Read the relevant sections of _docs/spec.md, _docs/architecture.md,
_docs/data-model.md and _docs/testing-guidelines.md.
First summarize the behavior, API, data changes, security checks,
edge cases and exact acceptance criteria. Then implement only that slice.
Use PostgreSQL transaction boundaries, ownership checks, audit logging,
Decimal/NUMERIC money handling where applicable, and idempotent jobs.
Write backend, frontend and E2E tests for relevant success/failure cases.
Update focused design/API documentation. Run tests and report results.
Finally review root README.md: update only if reader-facing information
changed, confirm commands/links work, and report the README verdict.
Commit cohesive changes; do not silently add another feature.
```

**Useful feature test matrix:** tutor/student/room double-booking; guardian accessing another family; tutor viewing another tutor's payroll; declined assignments; attendance correction after payroll finalization; duplicate payment request; partial family payment; course enrollment after start; cancel/no-show billability; retrying a periodic billing job twice.

## 5. Build and operations prompts

Use these only once relevant vertical slices are functional locally.

**Containerization:**

```text
Build production Compose stacks following _docs/architecture.md:
infra stack for PostgreSQL + PgBouncer, app stack for Caddy + built React
assets + FastAPI + separate Procrastinate worker. Preserve encrypted EBS
mount expectations, private Docker networking, health checks and volumes.
Test container restarts and migrations; update root README only with
verified local commands and requirements. Report README verdict.
```

**Terraform and deployment:**

```text
Provision independently configurable DEV, PROD and monitoring EC2 using
Terraform. DEV and PROD have separate PostgreSQL data on dedicated gp3 EBS,
CloudFront/Caddy HTTPS, restricted origin access, SSM management (no SSH),
S3 file storage, and pgBackRest + WAL archiving. Protect state and data from
accidental destroy. Validate plans and security boundaries; do not apply
PROD changes without approval. Document working procedures in _docs/;
update only the concise deployment/status section in root README as needed.
```

**CI/CD:**

```text
Set up GitHub Actions: PR checks; auto-DEV deployment from develop;
manual approval for PROD from main; push immutable images to ECR, promote
the same digest, use AWS OIDC and SSM Run Command, verify health and
support image rollback. Test workflow permissions and failure paths.
Review root README after the prompt and report update/no-update.
```

**Observability:**

```text
Instrument FastAPI and workers with OpenTelemetry and deploy separate
monitoring EC2 for Grafana/Prometheus/Loki/Tempo/Collector. Scope network
access, retention and dashboard-only alerts. Include health, request/error
latency, scheduler backlog, database, booking conflicts, job failures and
financial adjustment metrics without leaking personal data.
Keep operational details in _docs/, only a brief status/link in root README
if relevant. Report README verdict.
```

## 6. Completion checklist for every prompt

- [ ] Work is bounded to the requested slice; approved architecture respected.
- [ ] Authorization, ownership, edge cases and data integrity addressed.
- [ ] Relevant automated tests run; failures and limitations stated plainly.
- [ ] Focused docs/API/schema updated when applicable.
- [ ] **Root README reviewed; changed if needed, otherwise left unchanged.**
- [ ] README claims verified; report `README updated: ...` or `README reviewed: no update necessary`.
- [ ] Code committed with a meaningful message (unless explicitly requested otherwise).

## 7. Recommended first session

Begin with **Prompt 0**, review its architecture output, then execute **Prompt 1**. Use the reusable feature prompt for all subsequent slices. In this workflow **the user decides which prompt to run next**; do not create a parallel task backlog that silently directs implementation.
