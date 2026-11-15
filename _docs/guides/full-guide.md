# Classora — AI-Native Development Playbook

> **Project:** Classora — Tutoring & Test Preparation Center Management Platform  
> **Purpose:** A practical, specification-driven workflow for building, testing, deploying, and operating Classora with AI coding assistants.  
> **Status:** Development guide; no application code or cloud resources have been created by this document.  
> **Companion source of truth:** [`classora_project_specification.md`](classora_project_specification.md)

## Overview

Classora is a responsive, bilingual (Spanish and English) web platform for **one tutoring center at one location**. It covers individual tutoring, recurring group tutoring, structured courses, student and guardian accounts, academic progress, tutor payroll, payments, expenses, reports, and administrative workflows.

The functional scope and architecture have already been discussed and agreed. **Do not reopen settled technical choices or silently add features.** This playbook adapts the AI-assisted development lifecycle in the supplied `FULLREADME.md` to Classora: specifications → backlog → agent context → grooming → implementation → independent QA → integration → infrastructure → delivery → observability.

### Non-negotiable product rules

- Seven roles: **Owner, Administrator, Academic Coordinator, Finance Staff, Tutor, Student, Parent/Guardian**.
- Only administrators can finalize class bookings, course enrollments, withdrawals, and tutor assignments. Students and parents may request tutoring slots, subject to approval.
- Tutor assignments can be accepted or declined; teaching hours must be reviewed and approved before payroll.
- Parent accounts may link to multiple students, and students may have multiple guardians. Student login is optional.
- Financial corrections use reversals or adjustments. Preserve original payments, charges, payroll records, and historical rates.
- No online payments, electronic CFDI invoicing, multi-location tenancy, CRM, or student/parent messaging in the agreed scope.
- In-app notifications only; external infrastructure alert emails were **not** selected.
- Full functional scope is the target for first public release, but implementation should still proceed through small, testable vertical slices.

---

# Part 1 — Specification-driven development

## 1. Preserve the existing project decisions

Keep the consolidated specification in the repository before coding:

```bash
mkdir -p classora/_docs/{adr,team}
cd classora
# Copy the existing Classora specification into _docs/spec.md
```

**Authoritative documents:**

| File | Purpose |
|---|---|
| `_docs/spec.md` | User roles, requirements, business rules, and scope |
| `_docs/architecture.md` | Chosen technology stack and deployment architecture |
| `_docs/data-model.md` | Database entities, keys, constraints, lifecycle rules |
| `_docs/api.md` | REST conventions and API contract guidance |
| `_docs/design-system.md` | Tokens, component rules, accessibility, responsive patterns |
| `_docs/testing-guidelines.md` | Test strategy and critical scenarios |
| `_docs/process.md` | Issue lifecycle, roles, Definition of Done |
| `_docs/adr/` | Architecture Decision Records; later decisions supersede earlier ones |
| `AGENTS.md` | Short entry point for coding agents |
| `CLAUDE.md` | Pointer to `AGENTS.md` for Claude Code |

### Prompt: verify the specification before writing code

```text
Read _docs/spec.md. Create _docs/architecture.md using the decisions already
made for Classora. Do not change technologies or introduce a new architectural
style. Identify only concrete ambiguities, contradictions, and missing
implementation details. For each issue, suggest a resolution and mark it as
PROPOSED, not APPROVED. Do not write application code yet.
```

**Decision hierarchy:** explicit later decisions > earlier decisions > proposed defaults. If documents conflict, flag the conflict rather than silently choosing a different system.

## 2. Final technology decisions

| Concern | Classora choice |
|---|---|
| Frontend | React + TypeScript + Vite; feature-based structure |
| UI and forms | Tailwind CSS + shadcn/ui; React Hook Form + Zod |
| Frontend state | TanStack Query + Zustand |
| Router and i18n | React Router; react-i18next |
| Calendar and analytics | FullCalendar; Recharts + TanStack Table |
| UI quality | Storybook; WCAG 2.2 AA target |
| API | Python + FastAPI; `/api/v1` endpoints; OpenAPI-generated TypeScript client |
| Validation/config | Pydantic operation-specific models; pydantic-settings |
| Persistence | PostgreSQL; async SQLAlchemy 2.0 + asyncpg; Alembic |
| Database connections | PgBouncer, if benchmarking justifies its overhead on micro instances |
| Primary keys | UUID; normalized relationships; `TIMESTAMPTZ` |
| Transactions | Unit of Work, idempotency for financial operations, optimistic concurrency |
| Auth | Email/password, server-side PostgreSQL sessions, secure HTTP-only cookies, RBAC plus resource-level checks |
| Async work | Procrastinate: PostgreSQL-backed workers and periodic jobs; **no Redis or Celery** |
| Documents | Private S3-compatible object storage, presigned uploads, validation and malware scanning |
| Reports | WeasyPrint PDFs; openpyxl workbooks |
| Development tools | uv, Ruff, mypy; pnpm, ESLint, Prettier, strict TypeScript |
| Tests | pytest, Vitest, Testcontainers, Playwright |
| Local | Docker Compose, Makefile |
| Deployment | Separate DEV and PROD EC2 instances in one AWS account |
| Stateful storage | Self-hosted PostgreSQL on dedicated EBS gp3 volume for each environment |
| Database backups | pgBackRest + S3 with WAL archiving and recovery testing |
| Network | Route 53 → CloudFront → Caddy → app; HTTPS to origin; Elastic IP per environment |
| CI/CD | GitHub Actions, GitHub OIDC, SSM Run Command, ECR, automatic DEV, approved PROD |
| IaC | Terraform, encrypted/versioned S3 state, isolated environment states |
| Telemetry | OpenTelemetry → dedicated monitoring EC2 with Grafana, Loki, Prometheus, Tempo |

**Explicit supersessions:** Earlier references to RDS, ECS/Fargate application hosting, Redis, Celery, two environments without deployed DEV, or managed telemetry storage are obsolete. Classora uses **Docker Compose on EC2** in both deployed environments and **PostgreSQL-backed Procrastinate** jobs.

## 3. Turn scope into a backlog

Create short, independent vertical-slice tasks. One task should be reasonably completable and verifiable in a single coding session. The first task is **an empty runnable project with one passing test**.

### Prompt: create the initial backlog

```text
Read _docs/spec.md and _docs/architecture.md. Generate _docs/tasks.md.

Each task must fit in one coding session, be understandable independently,
and include: Goal, Description, Acceptance criteria, Dependencies, Out of scope,
Constraints, and Tests.

Start with a working project skeleton, Docker Compose, and one passing test.
Organize the remaining tasks as vertical feature slices, not as one giant
frontend ticket and one giant backend ticket. Respect Classora's full release
scope but order the work so each milestone produces something demonstrable.
Do not write implementation code.
```

### Suggested delivery sequence

1. **Foundation:** monorepo, toolchain, Docker, health endpoints, PostgreSQL, Alembic, CI, base layouts.
2. **Identity and access:** accounts, email/password, sessions, roles, guardian-student links, account lifecycle.
3. **Operational catalog:** subjects, education levels, tutor qualifications, availability, classrooms.
4. **Scheduling:** individual bookings, recurring sessions, conflict prevention, overrides, substitutions, assignments.
5. **Courses and academics:** groups, course templates/cohorts, enrollment, attendance, summaries, learning plans, evaluations, assignments.
6. **Billing and payments:** pricing history, packages, recurring tuition, charge generation, partial payments, receipts, cancellations/adjustments.
7. **Payroll and expenses:** time approval, fixed/variable rates, periods, approvals, pay slips, operating expenses.
8. **Documents and insights:** S3 upload, malware scan, shared materials, role-specific dashboards, reports and exports.
9. **Hardening and release:** permissions audit, accessibility, backup/restore tests, monitoring, recovery, security tests.

Review and refine the task backlog before syncing it to GitHub Issues. After issues have been created, **GitHub Issues become the canonical active backlog**; local documents remain durable reference material.

## 4. Issue template

Store at `_docs/task-template.md`:

```markdown
## Goal
What should be true when this issue is complete?

## Acceptance criteria
- [ ] A testable, observable result
- [ ] Permission and boundary cases are covered
- [ ] Errors and edge cases behave as specified

## Dependencies
- Related prerequisites or issues

## Out of scope
- Explicitly excluded items; link follow-up issues when needed

## Constraints
- Files/modules in scope
- Architecture and business rules to preserve
- Expected tests and documentation changes
```

### Example Classora backlog item

```markdown
## Goal
An administrator can book an individual tutoring session without double-booking
its tutor, student, or classroom.

## Acceptance criteria
- [ ] An authorized administrator creates a session in the calendar.
- [ ] The tutor must be qualified and available.
- [ ] Overlapping tutor, student, or room assignments are rejected.
- [ ] A permitted override requires a reason and is audit logged.
- [ ] A parent cannot directly confirm a booking.
- [ ] Concurrent conflicting booking attempts cannot both succeed.

## Dependencies
- Identity, tutor qualifications, student records, classroom catalog

## Out of scope
- Course cohorts, recurring class templates, billing (separate issues)

## Constraints
- FastAPI domain module + Unit of Work + PostgreSQL
- Operation-specific Pydantic schemas and generated OpenAPI client
- pytest integration tests and Playwright happy-path test
```

---

# Part 2 — Context engineering and agent workflow

## 5. Repository entry point: AGENTS.md

Keep `AGENTS.md` short so agents know where to look instead of receiving the entire project specification every time.

```markdown
# Classora agent instructions

Before coding:
- Read _docs/spec.md and _docs/architecture.md for relevant requirements.
- Read _docs/process.md, then the current GitHub issue.
- For UI work, read _docs/design-system.md.
- For API or persistence work, read _docs/api.md and _docs/data-model.md.
- For tests, read _docs/testing-guidelines.md.

Commands:
- make dev — launch local stack
- make test — run all tests
- make lint — run quality checks
- make migrate — apply Alembic migrations
- make seed — load development reference data
- make e2e — run Playwright tests

Rules:
- One groomed issue at a time. Do not invent new scope.
- Preserve financial history and role/resource-level access controls.
- No Redis, Celery, RDS, or public PostgreSQL ports.
- Keep implementation inside the relevant domain/feature boundary.
- Add tests and documentation for changed behavior.
- Do not mark an issue complete until independent QA passes.
- Commit small, meaningful changes with clear messages.
```

`CLAUDE.md` can contain only:

```markdown
@AGENTS.md
```

## 6. Workflow document: _docs/process.md

```markdown
# Classora delivery process

- GitHub Issues are the canonical backlog after import.
- Work on one issue at a time, or overlap at most two with each in its own worktree and its own QA PASS and merge.
- PM refines the issue before engineering starts.
- Engineer implements the groomed issue, writes tests, and commits changes.
- QA verifies each acceptance criterion independently and posts PASS or FAIL.
- On FAIL, the engineer fixes the issue and QA checks it again.
- The orchestrator closes the issue only after QA PASS.
- Do not silently add requirements; create a follow-up issue for new scope.
- Use pull requests and CI checks before merging.
- DEV receives approved `develop` merges automatically.
- PROD requires GitHub Environment manual approval.
```

## 7. Product Manager agent

Place in `_docs/team/pm.md`:

```text
You are Classora's Product Manager. Groom one GitHub issue at a time.
Read the issue and relevant sections of _docs/spec.md. Rewrite acceptance
criteria to be observable, testable, and unambiguous. Consider authorization,
minors' data, financial correctness, scheduling conflicts, edge cases, and
failure behavior. Record dependencies, constraints, and out-of-scope items.
Create follow-up issues instead of silently dropping requirements.
Do not write code. Stop when a new engineer can implement the issue without
making product decisions.
```

## 8. Software Engineer agent

Place in `_docs/team/software-engineer.md`:

```text
You are a Classora Software Engineer. Implement one groomed issue at a time.
Follow acceptance criteria, architectural boundaries, and the selected stack.
Write backend, frontend, integration, or E2E tests as appropriate. Do not
change acceptance criteria or close the issue yourself. Run relevant checks,
commit the code, and comment on the issue with implemented work, tests run,
and any unresolved limitation. If a requirement conflicts with another,
explain the conflict on the issue before proceeding.
```

## 9. QA Engineer agent

Place in `_docs/team/qa-engineer.md`:

```text
You are Classora's independent QA Engineer. Inspect running behavior, code,
and test results against the groomed issue's acceptance criteria. Verify every
criterion and probe authorization, duplicate financial actions, scheduling
races, and failures as relevant. Do not modify code. Comment with QA: PASS or
QA: FAIL, criterion-by-criterion evidence, exact test commands/results, and
reproduction steps for failures. Any unmet criterion means FAIL.
```

## 10. Orchestrator

The primary agent coordinates PM → Engineer → QA and does not certify its own implementation.

```text
Pick one eligible GitHub issue, ask PM to groom it, ask Engineer to implement
it, and ask QA to validate it. On FAIL, return the issue to Engineer with the
QA evidence. On PASS, close the issue and choose the next ready one. Respect
human approval requirements for production and infrastructure changes.
Never treat a tool's execution as proof of success without checking results.
```

Automated agent loops are **optional orchestration**, not a feature guaranteed by all coding assistants. Start manually if the selected coding tool lacks safe delegation or pause/resume controls.

---

# Part 3 — Build Classora incrementally

## 11. Bootstrap the monorepo

Recommended repository layout:

```text
classora/
├── apps/
│   ├── web/                  # React, Vite, TypeScript
│   │   └── src/features/     # by business feature
│   └── api/                  # FastAPI + Procrastinate codebase
│       ├── app/
│       │   ├── core/
│       │   ├── modules/      # identity, scheduling, academics, etc.
│       │   └── workflows/    # cross-domain application operations
│       ├── migrations/
│       └── tests/
├── packages/api-client/     # generated from FastAPI OpenAPI
├── infrastructure/
│   ├── terraform/
│   ├── docker/
│   ├── observability/
│   └── scripts/
├── _docs/
│   ├── architecture.md
│   ├── spec.md
│   ├── data-model.md
│   ├── api.md
│   ├── design-system.md
│   ├── testing-guidelines.md
│   ├── process.md
│   ├── adr/
│   └── team/
├── e2e/
├── .github/workflows/
├── compose.yaml
├── Makefile
├── AGENTS.md
└── CLAUDE.md
```

### Prompt: bootstrap

```text
Create the Classora monorepo skeleton exactly as documented. Set up a minimal
React/Vite app, a FastAPI app with /health/live and /health/ready, PostgreSQL
through Docker Compose, Alembic migrations, a Procrastinate worker placeholder,
and Makefile commands. Add one passing test for each app and a GitHub Actions
CI workflow that runs those tests. Use uv and pnpm. Do not implement business
features yet. Confirm the stack runs locally before marking the task done.
```

## 12. Frontend-first prototypes with a mock API

Begin by prototyping role-specific flows, keeping all backend calls behind one typed service boundary. Mocked data should exercise realistic behavior, not bypass selected business rules.

**First prototypes:** admin scheduling calendar; student/guardian profile; tutor teaching dashboard; billing and payroll overview.

```text
Build an interactive React prototype for Classora's administrator scheduling
experience using Tailwind, shadcn/ui, FullCalendar, TanStack Query, and Zustand.
Keep API calls in a centralized service layer with a mock implementation.
Include tutors, student profiles, availability, classrooms, class status,
conflict feedback, and pending assignment approvals. Use realistic synthetic
data. Do not add user-controlled direct enrollment or automatic tutor booking.
Include tests and loading/empty/error states. Do not build a real backend yet.
```

Verify early with stakeholders before investing in each corresponding backend domain.

## 13. OpenAPI contract and typed frontend client

FastAPI is the source of truth for the **implemented** OpenAPI specification. During mock-first prototyping, draft the expected operations first and reconcile them with FastAPI before connecting the frontend. Generate the TypeScript client automatically; avoid hand-maintained duplicate contracts.

```text
Read the approved Classora scheduling requirements and the mock frontend
service interface. Propose versioned REST endpoints under /api/v1, including
methods, request/response Pydantic schemas, auth requirements, error responses,
and pagination. Review the contract for resource-level authorization and
concurrent booking conflicts. Once approved, implement FastAPI routes and
generate the TypeScript client from its OpenAPI schema. Add contract tests.
```

Use RFC 9457 Problem Details for API errors. Use cursor pagination for large transaction/audit collections and offset pagination for ordinary admin tables.

## 14. Database first for financial truth

The example README uses temporary in-memory and SQLite stages. **Classora should use PostgreSQL from the beginning** because its business-critical behavior depends on PostgreSQL transactions, locks, `TIMESTAMPTZ`, full-text search, and Procrastinate.

Suggested design sequence:

1. Identity, users, roles, guardians, and students.
2. Tutors, qualifications, availabilities, classrooms, and sessions.
3. Course templates, cohorts, enrollments, and attendance.
4. Academic observations, assignments, submissions, and evaluations.
5. Charges, credits, payments, allocations, and adjustments.
6. Payroll periods, approved teaching entries, rates, and payments.
7. Files, audit events, preferences, settings, notifications, and jobs.

### Prompt: model a domain

```text
Design Classora's scheduling domain in PostgreSQL. Provide an ER diagram,
normalized tables with UUID keys, foreign keys, unique constraints, indexes,
status transitions, and invariants. Explain recurring RRULE sessions,
exceptions, tutor substitutions, and booking conflict prevention including
concurrent requests. Identify where optimistic version checks and PostgreSQL
locking or exclusion constraints are required. Do not generate Alembic
migrations until the schema has been reviewed.
```

**Important:** optimistic version numbers are appropriate for stale form updates, but may not alone prevent two simultaneous new bookings from creating overlapping records. Verify conflicting inserts at the database transaction level.

## 15. Implement one vertical slice end-to-end

For every groomed issue:

1. Review expected behavior and permissions.
2. Add the migration and tests.
3. Implement domain service and Unit of Work transaction.
4. Expose the versioned FastAPI endpoint.
5. Regenerate the typed React API client.
6. Replace the feature's mock adapter with real API calls.
7. Add Playwright scenarios with role-specific accounts.
8. Conduct independent QA and preserve evidence.

For instance, a scheduling slice is complete only when an administrator can create a real session, a tutor can acknowledge or decline assignment, a parent sees only authorized student information, and concurrent conflicts are handled correctly.

## 16. Key product workflows to test

| Workflow | Required test focus |
|---|---|
| Booking | Tutor/student/room collisions, availability, overrides, pending approval |
| Recurrence | Exceptions, holiday skipping, single occurrence edits, timezone behavior |
| Enrollment | Admin-only enrollment, mid-course changes, charge adjustments, capacity |
| Attendance | Tutor submission, admin corrections, audit log |
| Teaching hours | Approval, correction requests, rate effective dates, payroll calculation |
| Billing | Duplicate job execution, partial payment allocations, credit consumption, reversals |
| Payroll | Salary + extra teaching pay, prorating, approval sequence, immutable finalized periods |
| Family accounts | Multiple children/guardians; no cross-family data leakage |
| Files | Presigned upload, size/type check, malware scan, authorized download, recovery |
| Reports | Date filters, permitted fields, MXN precision, exports |

For money, use exact decimal arithmetic, clear rounding conventions, and transaction-level invariants. Do not use binary floating point for monetary amounts.

---

# Part 4 — Testing and local developer experience

## 17. Test strategy

- **Backend:** pytest for units and HTTP API integration; Testcontainers for isolated PostgreSQL-backed tests.
- **Frontend:** Vitest + React Testing Library; Storybook for component states.
- **End-to-end:** Playwright with independent role sessions and synthetic seeded data.
- **Migration checks:** Alembic upgrade tests and CI detection of unintended schema drift.
- **Security:** dependency scans, secret detection, SAST, container scanning, authorization tests.
- **Accessibility:** automated accessibility checks targeting WCAG 2.2 AA, supplemented by manual validation for critical interactions.

### Example Playwright acceptance scenario

```text
Add a Playwright end-to-end test for Classora:
1. Log in as a parent linked to Student A.
2. Request an individual tutoring session in an available slot.
3. Verify the request is pending and not confirmed.
4. Log in separately as an administrator and assign a qualified tutor.
5. Log in as that tutor and accept the assignment.
6. Verify the session is confirmed for the correct student/guardian.
7. Attempt a conflicting booking and verify rejection.
8. Log in as an unrelated parent and verify Student A's data is inaccessible.
Use synthetic data and document the assertions and actual test results.
```

## 18. Development commands

Expose consistent commands through `Makefile` (implementations are to be created in the repository):

| Command | Purpose |
|---|---|
| `make help` | Discover developer commands |
| `make install` | Install uv and pnpm dependencies |
| `make dev` | Start local Docker Compose stack |
| `make backend` | Start/reload FastAPI locally |
| `make frontend` | Start Vite dev server |
| `make test` | Backend and frontend unit/integration tests |
| `make e2e` | Playwright tests against local stack |
| `make lint` | Ruff, mypy, ESLint, TypeScript checks |
| `make migrate` | Apply Alembic upgrades |
| `make seed` | Seed idempotent reference data and synthetic dev data |
| `make build` | Build production application images |
| `make logs` | Inspect local Docker service logs |

Run development with Docker Compose; never expose PostgreSQL on a public network interface.

---

# Part 5 — Docker, AWS, and CI/CD

## 19. Container design

Classora has two deployed application environments, **DEV** and **PROD**, each on its own configurable EC2 instance in the same AWS account. Both use Docker Compose, with separated stateful and application stacks.

```text
DEV EC2                          PROD EC2
  app stack                        app stack
    Caddy                            Caddy
    React static assets              React static assets
    FastAPI                          FastAPI
    Procrastinate worker             Procrastinate worker
    PgBouncer                        PgBouncer
  infrastructure stack             infrastructure stack
    PostgreSQL                       PostgreSQL
  gp3 EBS data volume              gp3 EBS data volume
```

Use a **multi-stage frontend build**. React's compiled static files can be served from Caddy without a permanently running Vite development server. FastAPI and Procrastinate can use the same backend image with different entry commands.

Include Docker health checks, `restart: unless-stopped`, systemd stack startup configuration, and resource limits. For low-memory EC2 instances, verify the cost and overhead of every container before shipping.

### Prompt: containerize

```text
Create production Dockerfiles and Docker Compose configurations for Classora.
Build the React frontend with pnpm and serve its static output via Caddy.
Run FastAPI and Procrastinate in separate containers from the same Python
image. Keep PostgreSQL in a separate infrastructure Compose project on an
attached EBS-backed host path. PgBouncer, if enabled, must not expose a public
port. Define health checks, restart policies, and environment variables.
Do not introduce Redis, Celery, RDS, or Kubernetes.
```

## 20. Provision AWS using Terraform

Terraform should manage:

- One AWS account with independently deployed DEV and PROD EC2 instances.
- Dedicated Elastic IP and EBS gp3 database volume per environment.
- Route 53 DNS records, separate CloudFront distributions, HTTPS origin hostnames.
- Caddy HTTPS origin with public certificate and certificate-renewal strategy.
- S3 buckets for private uploaded documents and pgBackRest backups/WAL.
- Amazon ECR with immutable image references and lifecycle retention.
- IAM instance roles, GitHub OIDC deployment roles, SSM access, restricted security groups.
- A dedicated, independently sized monitoring EC2 instance.
- Versioned/encrypted S3 remote Terraform state with state locking and separate states for DEV, PROD, monitoring.

Treat destructive changes to EBS, S3, and critical state as approval-gated even in DEV. Use `prevent_destroy` where appropriate and maintain recovery procedures.

**Network caution:** CloudFront's origin-facing IP restrictions reduce direct reachability but do not by themselves prove a request came through *your* distribution. Consider a CloudFront-injected origin secret header as an additional control. Plan certificate issuance/renewal when origin traffic is restricted.

## 21. GitHub Actions workflow

```text
feature/* -> PR -> develop -> automatic DEV deployment
                         |
                         +-> tested image in Amazon ECR
                                       |
main / approved release -> GitHub Environment approval -> PROD
                          (same ECR image digest)
```

**Pipeline steps:**

1. Validate formatting, type checks, tests, security scans, and Terraform plan.
2. Run PostgreSQL/Testcontainers integration tests and Playwright E2E tests.
3. Build immutable Docker image(s), tag `vMAJOR.MINOR.PATCH-YYYYMMDD` and Git SHA; push to ECR.
4. On `develop`, automatically deploy the exact image digest to DEV using GitHub OIDC + SSM Run Command.
5. Run Alembic migration step and application readiness/smoke checks.
6. Promote a successfully tested DEV image digest to PROD only after GitHub approval.
7. If application health fails, roll back to the previous healthy image; never assume database schemas have rolled back.

When identical version/date tags could be reused for multiple builds, enforce uniqueness using the Git SHA or immutable digest. Tags must not substitute for digest-based promotion.

### Prompt: implement CI/CD

```text
Create GitHub Actions workflows for Classora.
- Pull requests: lint, typing, units, PostgreSQL integration, Playwright,
  security scans, and Terraform validate/plan.
- On merge to develop: build the container image(s), push to ECR, deploy to
  DEV via AWS OIDC and SSM Run Command, and verify readiness.
- For PROD: require GitHub Environment approval and promote the same tested
  image digest from DEV, without rebuilding.
- Support dated semantic release tags plus immutable Git SHA/digest.
- Keep migrations backward compatible and block deployment on migration failure.
- Require approval for potentially destructive Terraform changes in any env.
Do not store long-lived AWS access keys or embed secrets in images.
```

## 22. Configuration and secrets

The selected configuration approach uses `.env` locally and **GitHub Secrets delivered to EC2 through SSM** for deployments. Use `pydantic-settings` to validate settings at service startup. Keep secrets out of Git, image layers, Terraform state outputs, logs, and command-line history. Design the transfer carefully to avoid exposing secrets in SSM command payloads/logs.

PostgreSQL should use separate least-privileged credentials for the API, background workers, migrations, and backups.

## 23. PostgreSQL protection and recovery

- Keep database files on dedicated persistent EBS, not inside ephemeral container storage.
- Use `pgBackRest` with S3 backups and continuous WAL archiving for PITR.
- Confirm backups succeed, retain only approved periods, and practice restoring to a clean instance.
- Preserve financial history through immutable entries, not raw row deletion.
- Use explicit application idempotency keys for charges and payroll and PostgreSQL constraints for invariants.

A successful upload to S3 or EBS snapshot is **not** sufficient proof that a PostgreSQL backup is recoverable. A restore test is part of release readiness.

---

# Part 6 — Observability and operations

## 24. Independent observability instance

A dedicated monitoring EC2 hosts the self-managed stack:

```text
DEV / PROD apps
     |
OpenTelemetry instrumentation
     |
OpenTelemetry Collector
     |-------------------|-------------------|
 Prometheus             Loki                Tempo
  (metrics)             (logs)              (traces)
     \                   |                  /
                     Grafana
```

Use HTTPS and authentication for Grafana. Restrict OTLP ingestion to known app instances. Store telemetry with individually configurable retention (e.g., metrics 30 days, logs 14 days, traces 7 days—*illustrative only*). Selected alerts are **visible in Grafana dashboards**, not automatically emailed.

### Application-specific measurements

- Booking requests submitted/approved/declined and booking conflicts.
- Completed sessions, pending teaching-hour approvals, rejected hour corrections.
- Failed or retried recurring billing jobs; duplicate financial attempts blocked.
- Procrastinate queue backlog, failed tasks, processing latency.
- API response latency, errors, PostgreSQL slow queries, DB disk and memory pressure.
- Upload scan failures, report generation errors, unresolved sessions.

Do not include passwords, session cookies, student personal documents, or sensitive financial payloads in telemetry.

## 25. Operational runbooks

Create and exercise concise runbooks for:

- Replacing an EC2 instance while reattaching its EBS volume.
- Restoring PostgreSQL with pgBackRest/PITR.
- Rolling back a failed application image.
- Recovering failed Procrastinate tasks and preventing duplicate charges.
- Rotating credentials and invalidating leaked sessions.
- Investigating full disks, unhealthy containers, or failed HTTPS certificates.
- Handling corrupt uploads and privacy incidents.

The dedicated monitoring EC2 should not become a hidden dependency that stops the business API whenever telemetry is unavailable.

---

# Part 7 — Definition of Done and first implementation session

## 26. Definition of Done

A feature is done only when:

- Its GitHub issue has groomed, testable acceptance criteria.
- It follows the Classora specification and approved ADRs.
- Backend authorization and record ownership are enforced and tested.
- API and frontend behavior, loading/error states, and translations are covered where relevant.
- Migrations and transaction boundaries are correct.
- Automated tests pass, including regression tests for critical financial/scheduling changes.
- Security, privacy, and audit expectations are met.
- QA independently posts **PASS**, code is reviewed, and CI is green.
- User-facing documentation and operational runbooks are updated if relevant.

## 27. Recommended first coding prompt

```text
You are starting the Classora repository.
Read AGENTS.md, _docs/spec.md, and _docs/architecture.md.

Implement only the first foundation issue:
1. Create the monorepo scaffold with React/Vite/TypeScript and FastAPI.
2. Add Docker Compose with PostgreSQL and a minimal FastAPI readiness check.
3. Configure uv, pnpm, Ruff, mypy, ESLint, Prettier, and a Makefile.
4. Add a minimal Alembic migration, one passing backend test, and one passing
   frontend test.
5. Make `make dev`, `make test`, and `make lint` run successfully.
6. Record exact commands and results in the issue comment.

Do not add feature screens, payroll rules, bookings, or cloud resources yet.
Do not introduce Redis or Celery. Do not close the issue; independent QA will
validate it.
```

## 28. What comes after the foundation

Once the first slice runs locally, prioritize **identity/family modeling** and **scheduling**. Scheduling establishes the most important cross-domain invariants (time, capacity, tutor eligibility, approval). **Billing and payroll** can then consume approved sessions and time entries without bypassing auditability.

### Final reminder

This document is a **development playbook**, not a replacement for Classora's detailed product specification. The companion `classora_project_specification.md` remains the scope reference. This guide describes *how to implement and operate* that scope with repeatable AI-assisted engineering practices, adapted from the supplied `FULLREADME.md`.
