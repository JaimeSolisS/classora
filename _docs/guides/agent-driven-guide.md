# Classora — Agent-Driven Development Guide

**Workflow:** GitHub Issues are the **only active implementation backlog**. A Product Manager agent grooms, a Software Engineer agent implements, and a QA agent verifies. An orchestrator manages transitions. Prompts here **operate on issues**; they are not a parallel set of build instructions.

> This is an implementation guide, not the public root `README.md`. The canonical product scope is `_docs/spec.md` and architecture is `_docs/architecture.md`. Do not silently replace approved choices.

## 1. Shared baseline and repository layout

Classora is a bilingual (Spanish/English), MXN-only management platform for **one** tutoring/test-preparation center. It handles role-scoped tutoring operations, courses, students and guardians, scheduling, assessments, files, billing, payroll, expenses and analytics.

Approved stack: React/Vite/TypeScript + Tailwind/shadcn; FastAPI + async SQLAlchemy/asyncpg/Alembic; PostgreSQL + PgBouncer; **Procrastinate backed by PostgreSQL, with a separate worker container and periodic jobs**; Docker Compose; AWS EC2 + Terraform; ECR + GitHub Actions + SSM; Caddy + CloudFront; S3 and pgBackRest; OpenTelemetry + Grafana/Prometheus/Loki/Tempo. **No Redis, Celery, RDS, or automatic banking transfers.**

```text
classora/
├── README.md                     # Human-facing overview, kept current
├── AGENTS.md                     # Universal agent instructions and README gate
├── CLAUDE.md                     # @AGENTS.md
├── apps/
│   ├── web/
│   └── api/
├── packages/api-client/
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
│   ├── process.md
│   ├── task-template.md
│   ├── adr/
│   └── team/
│       ├── pm.md
│       ├── software-engineer.md
│       └── qa-engineer.md
├── e2e/
├── .github/workflows/
├── compose.yaml
└── Makefile
```

Do not create a redundant `docs/`, `frontend/` or `backend/` directory.

## 2. One backlog, multiple supporting documents

| Source                           | Authority                                                                                     |
| -------------------------------- | --------------------------------------------------------------------------------------------- |
| `_docs/spec.md`                  | **What** Classora must do and acceptance-level product constraints                            |
| `_docs/architecture.md` and ADRs | **How** it must be built and documented technical exceptions                                  |
| GitHub Issues                    | **What implementation work remains**, issue status, acceptance criteria, comments, QA verdict |
| `AGENTS.md` + `_docs/process.md` | **How agents work** and task lifecycle                                                        |
| `_docs/team/*.md`                | Role-specific agent instructions                                                              |
| Root `README.md`                 | **Human entry point**, current project overview and verified startup instructions             |

`_docs/tasks.md` is **temporary import material**, not a second living backlog. Once Issues are created and cross-checked, archive or delete the temporary file; link durable requirements back to `_docs/spec.md`.

## 3. Root README policy: mandatory gate after every issue

The root `README.md` must be the first useful page a human sees, **not** the entire specification, internal agent guide, or a chronological build diary. Keep it short, accurate, approachable:

- Product name, one-paragraph description and honest maturity/status.
- Brief list of **implemented** features (planned features clearly marked).
- Condensed architecture/stack.
- Prerequisites, minimum local setup, verified commands and configuration approach.
- Development/test commands that actually exist.
- Links to spec, architecture, agent workflow, and contribution guidance.
- Brief deployment information only once deployments exist.

Never copy issue acceptance criteria, long prompts, agent logs, internal runbooks, unverified URLs, AWS account details, passwords, or extensive change history into root README. Prefer links into `_docs/`.

**Mandatory gate for every issue:** After implementation **and again before QA PASS / closing**, determine whether that issue changed anything a new developer/user needs to know. If yes, update root README **in the same PR/issue** and verify instructions. If not, do not modify it. Every issue implementation comment and QA verdict must include one of:

- `README updated: <sections and verification>`
- `README reviewed: no update necessary`

A QA **PASS requires** a correct README verdict, plus accurate instructions if affected. A README gate is not satisfied by adding noise to the README. Each follow-up engineering issue must repeat the gate.

## 4. Seed the repository and backlog

**Prompt: verify the specification before writing code**

```text
Read _docs/spec.md. Create _docs/architecture.md using the decisions already
made for Classora. Do not change technologies or introduce a new architectural
style. Identify only concrete ambiguities, contradictions, and missing
implementation details. For each issue, suggest a resolution and mark it as
PROPOSED, not APPROVED. Do not write application code yet.
```

**One-time bootstrap:** establish root README, AGENTS.md, CLAUDE.md, `_docs/spec.md`, `_docs/architecture.md` and process docs. Infrastructure and feature implementation should be tracked as Issues, not executed directly via ad-hoc prompts.

**Prompt — generate planning backlog (no code):**

```text
Read _docs/spec.md and _docs/architecture.md.
Create _docs/tasks.md as a temporary import list of small independent
implementation tasks. First task: empty monorepo, verified local dev setup,
readiness endpoint, and one passing test. Organize by milestone and dependency.
Each task: goal, measurable acceptance criteria, dependencies, out of scope,
constraints, likely files, tests, and README impact check.
Cover backend, frontend, security, financial integrity, operations and QA.
No code. Do not change product scope. Review root README and report the
README verdict if the repo documentation changes.
```

**Prompt — publish backlog as issues:**

```text
Convert reviewed _docs/tasks.md into GitHub Issues for Classora.
Use milestone and labels (foundation, frontend, backend, database,
security, infra, testing, docs). Preserve acceptance criteria, dependencies,
and spec references. Avoid duplicates. Confirm the issue count and links.
Once confirmed, GitHub Issues become the only active backlog. Review README and report verdict.
```

These are backlog **creation** prompts, not permission to implement every task.

### Suggested milestones

1. Foundation and local developer experience.
2. Identity, security, students and guardians.
3. Tutors, qualifications, availability, rooms.
4. Scheduling, recurrence, approvals and courses.
5. Academics, documents, learning plans and reports.
6. Billing, credits, payments and adjustments.
7. Payroll, expenses and finance controls.
8. Administration, analytics, internationalization.
9. DEV/PROD deployment, recovery, observability.
10. Security verification and production readiness.

Introduce CI, tests and database resilience early rather than deferring everything to the final milestone.

## 5. Issue template (`_docs/task-template.md`)

```markdown
## Goal

A specific user-visible or operational result.

## Acceptance criteria

- [ ] A checkable success case.
- [ ] A checkable failure/authorization case.
- [ ] Tests and README review completed.

## Dependencies

- #<issue-number> or none.

## Out of scope

- Anything intentionally excluded (link follow-up Issues).

## Constraints

- Relevant `_docs/spec.md` sections and ADRs.
- Database/API boundaries, approved libraries, files changed.
- Privacy, auditing, migration and financial precision expectations.

## Verification

- Commands to run and observable outcome.

## README gate

- [ ] Reviewed root `README.md` and updated only if needed.
- [ ] Final issue comment states updated sections or no-update verdict.
```

## 6. Always-on AGENTS.md instructions

Place these rules into the real root `AGENTS.md` (do not make this guide an alternative instruction file):

```markdown
# Classora agent instructions

- Read relevant sections of `_docs/spec.md` and `_docs/architecture.md`.
- Follow `_docs/process.md`; GitHub Issues are the only active backlog.
- Work on exactly one groomed issue at a time unless orchestrator explicitly
  manages independently approved work.
- Do not implement extra features, adjust acceptance criteria during coding,
  or introduce Redis/Celery/RDS without a new approved ADR.
- Use tests, migrations and authorization checks suited to the change.
- Document findings and exact test commands/results in the issue.
- AFTER EVERY ISSUE OR FOLLOW-UP CODING PROMPT: review root `README.md`.
  Update only if public onboarding, implemented capabilities, tech stack,
  verified commands, deployment status or links changed. Never put task logs
  or internal notes in the README. Report one of:
  `README updated: ...` / `README reviewed: no update necessary`.
- QA must verify the README gate before PASS. The orchestrator must not close
  an issue without a QA PASS and README verdict.
```

`CLAUDE.md` can contain only `@AGENTS.md`.

## 7. Specialized agent roles

### Product Manager (`_docs/team/pm.md`)

```text
Given GitHub issue #N, consult its spec and architecture references.
Make acceptance criteria checkable and include success, failure, roles,
edge cases, dependencies, tests and explicit out of scope. Add a README
impact check to the issue. If details conflict with approved requirements,
comment and escalate rather than guessing. Do not implement code.
```

### Software Engineer (`_docs/team/software-engineer.md`)

```text
Implement ONLY groomed GitHub issue #N. Respect its acceptance criteria,
_docs/spec.md, _docs/architecture.md, and the relevant API/data/docs rules.
Add backend/frontend/E2E tests and migration checks as appropriate.
Run tests, commit changes, and comment the exact changes and commands/results.
Review root README.md, update reader-facing content only if needed,
verify any changed commands/links, and post a README verdict.
DO NOT close the issue or claim independent QA PASS.
```

### QA Engineer (`_docs/team/qa-engineer.md`)

```text
Independently verify GitHub issue #N against acceptance criteria and the
running application; inspect authorization, boundary conditions, and tests.
Verify the root README review verdict, and if README content changed,
check its commands/links for accuracy. Do not edit production code or
quietly fix acceptance criteria. Post QA: PASS or QA: FAIL with checkboxes,
reproduction steps, test commands/results, and README verification.
Any missing README gate or inaccurate affected README instructions = FAIL.
```

## 8. Orchestration lifecycle and prompts

The orchestrator coordinates work but does **not** silently substitute for PM, Engineering or QA.

```text
Select one ready GitHub issue
  → PM grooms and clarifies it
  → Engineer implements and performs README gate
  → QA independently verifies behavior AND README gate
     ├─ FAIL → engineer addresses findings, repeats README gate → QA again
     └─ PASS → orchestrator closes issue and selects next ready issue
```

**Manual prompts:**

```text
Groom GitHub issue #12 according to _docs/team/pm.md.
```

```text
Implement GitHub issue #12 according to _docs/team/software-engineer.md.
```

```text
QA GitHub issue #12 according to _docs/team/qa-engineer.md.
```

**Orchestration prompt (if your tool supports subagents, otherwise run manually):**

```text
Read AGENTS.md and _docs/process.md. Select one ready Classora GitHub issue.
Dispatch PM → Engineer → independent QA. If FAIL, engineer fixes and QA reruns.
Every engineer cycle must review root README.md; QA must verify the verdict.
Only after a documented QA PASS and green checks may you close that issue.
Do not process blocked issues or silently edit scope; record blockers.
Stop after this issue unless I explicitly request another.
```

Do not assume a `/goal` command exists: looping commands depend on the installed agent harness and its capabilities. Do not allow autonomous direct PROD deploys, destructive migrations or financial data edits without explicit human approval.

## 9. Example issue: tutor availability

**Issue title:** Add tutor weekly availability and exception dates.

- **Goal:** Tutors may submit availability; administrators see it while scheduling.
- **Acceptance:** scoped tutor updates, holiday/exception behavior, read permissions, conflict checks, backend enforcement; unauthorized updates return an appropriate error.
- **Out of scope:** booking calendar UI and payroll logic (own issues).
- **Tests:** API integration and role/ownership tests, UI tests for update/error states.
- **README impact:** likely **no update** for an internal feature increment unless newly visible functionality changes the project feature summary.

This issue links `_docs/spec.md` and `_docs/architecture.md`; it should not duplicate their full contents.

## 10. Definition of Done and release controls

An issue is complete only when:

- [ ] Groomed criteria are satisfied and scope is respected.
- [ ] Appropriate authorization, integrity, audit and edge-case checks exist.
- [ ] Tests pass, including PostgreSQL integration and relevant Playwright tests.
- [ ] Schema, OpenAPI, ADRs and focused docs are changed if required.
- [ ] **Root README gate completed and verified; no unnecessary edits.**
- [ ] Engineer commits and documents what was tested.
- [ ] Independent QA explicitly posts PASS.
- [ ] CI checks succeed and issue is closed by orchestrator/human.

Deployments remain separate GitHub Issues: immutable image digest to ECR, auto DEV from `develop`, approved PROD from `main`, Terraform safety gates, SSM deployments and health/rollback verification. Financial invariants and privacy must be tested at issue level, not left for a final QA sweep.

## 11. First actions

1. Check in the approved `_docs/spec.md` and `_docs/architecture.md`.
2. Add `AGENTS.md`, `_docs/process.md`, `_docs/task-template.md` and role descriptions with the README gate.
3. Seed a small backlog, review it, publish to GitHub Issues, then eliminate `_docs/tasks.md` as an active backlog.
4. Groom and implement the **foundation** issue; independently QA it; close only after PASS and README review.

**Key distinction from the prompt-driven guide:** implementation instructions live in individual issues and are executed by role-specific agents. The root README rule applies after **every issue** in exactly the same way as it applies after **every prompt** in the prompt-driven workflow.
