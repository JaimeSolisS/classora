# Classora — Tutoring Center Management Platform

**Product name:** Classora  
**Canonical product and architecture specification (`_docs/spec.md`)**  
**Version:** 1.2 — 2026-10-16  
**Status:** Requirements and architecture baseline; implementation not started. Detailed schema, API contracts and wireframes remain pending.  
**Companion guide:** `Classora_FULLREADME.md` (AI-assisted delivery workflow; not a substitute for product requirements).

> **Source of truth:** This document consolidates the project decisions discussed in the Classora planning conversation. Later corrections supersede earlier choices (notably: **no Redis or Celery; self-hosted PostgreSQL on EC2; independently deployed DEV and PROD; Procrastinate with PostgreSQL for background and periodic jobs**). Proposed entities and implementation details are identified as design starting points, not approved schema.
>
> **Document boundary:** This specification defines **what Classora must do and the technical constraints**. Agent roles, prompts, grooming, and the day-to-day delivery process belong to `Classora_FULLREADME.md`, `AGENTS.md` and `_docs/process.md`.

## 1. Product vision and boundaries

Classora is a bilingual (Spanish/English), responsive web application for **one tutoring and test-preparation center at one location**. The platform manages individual tutoring, ongoing group tutoring, and structured exam-preparation courses, with administrative scheduling, academic progress, parent/student access, tuition billing, tutor payroll, operating expenses, documents, and analytics.

- Currency: MXN only.
- A full-featured initial release was selected; implement in phases without reducing the target scope.
- Not a multi-tenant SaaS; no multi-branch operations in scope.
- No online student payment processing. Staff record payments made externally.
- No native mobile app; responsive browser experience.
- No CRM/prospect pipeline.
- In-app notifications only for product users; no email, SMS, WhatsApp or direct in-app messaging.
- No CFDI/SAT invoicing integration; produce branded non-tax payment receipts.
- Parents/students can request tutoring sessions, but **only administrators finalize bookings and enroll/withdraw students**.

## 2. User roles and permissions

- **Owner:** Full center oversight; financial reporting; final approvals and sensitive administration.
- **Administrator:** General operational management, scheduling, enrollment, user administration and permitted approvals.
- **Academic Coordinator:** Academic oversight, tutor assignments, verification of teaching hours, academic reporting.
- **Finance Staff:** Charges, payments, expenses, payroll preparation and payment tracking.
- **Tutor:** Manage availability and qualification requests; accept/decline assignments; submit attendance, actual hours, session summaries, assignments/evaluations, and correction requests; see own earnings.
- **Student:** Optional login; academic profile exists regardless. See own schedule, materials, assignments, grades and approved reports; request individual sessions.
- **Parent/Guardian:** Family account with multiple children, and multiple guardians linked to a child; sees linked children's academic records as permitted, scheduling, and consolidated family billing; can request individual sessions and cancellation/rescheduling.

Permissions use predefined roles plus record-level ownership checks. Tutors access academic records of current assignments and read-only historical records they taught. Parents access only linked children. Sensitive actions require password reauthentication. Internal tutor notes are not automatically shared with families. Tutors see only their own compensation details. Users can be pending, active, suspended or archived; deactivation revokes access but preserves authorized historical records.

## 3. Class, enrollment and scheduling rules

### Class types
1. **Individual tutoring:** One student, flexible subject/tutor/duration; one-off or recurring.
2. **Ongoing group tutoring:** Flexible continuing group with enrolled students and a group-specific capacity.
3. **Structured course:** Reusable template containing syllabus/modules, subjects, duration and materials; each cohort has its own dates, tutors, enrolled students, pricing and attendance. Course completion criteria include configurable attendance, modules and required assessments.

### Schedule administration
- Tutors publish recurring weekly availability and exceptions (time off, holidays, changes), preferred classrooms, teaching limits and required breaks. Changes affecting existing assignments require administrator approval.
- Tutors request subject + education-level qualifications, approved by an administrator.
- Administrators schedule all confirmed classes, make final tutor assignments, and manage group/course enrollment (including additions/removals after a start date with appropriate financial adjustments).
- Parents and students can see available individual-session slots and **request**, not automatically book, them.
- Tutors accept/decline offered assignments; declined assignments return for reassignment.
- The system recommends eligible tutors according to subject, level, schedule and workload; replacement tutor suggestions are available.
- Calendar: daily/weekly/monthly FullCalendar UI; filters by tutor, student, room, type; drag-and-drop rescheduling, validated server-side.
- Recurrence: iCalendar RRULE patterns, rolling generation of individual session records, per-occurrence exceptions, changes to future occurrences, holiday skipping, substitutes, cancellations.
- Center-wide holidays, closure dates and vacation periods affect recurring schedules.
- Classroom catalog is **basic**: name, capacity, availability. No required equipment inventory. Each group/cohort has its own maximum size and respects room capacity.
- Prevent student/tutor/room overlap and capacity violations. Recommend alternate slots; authorized administrators may override defined restrictions with reason and audit record.
- Workload limits produce warnings for daily/weekly excess; booking conflicts and capacity rules otherwise enforce restrictions as selected.

### Attendance, session completion and changes
- Tutors mark attendance present/absent/late; admins may correct it with audit history.
- Guided closeout combines attendance, actual teaching hours, structured summary (topics, performance, homework, next steps) and submission for approval.
- Approved hours feed payroll. Tutors can request corrections and admins reapprove; finalized payroll remains locked with adjustments applied to future periods.
- Cancellations and rescheduling follow configurable notice/no-show/makeup policies, with user requests and admin approvals, exception handling, and audit trails.
- Billing and tutor compensation are adjusted as appropriate. Rescheduling normally preserves an existing charge; cancellations or billable duration changes create adjustment entries.
- Parent/student schedules include linked attendance, homework, materials and session summaries where permitted.

## 4. Academic records and learning

- Student profile: personal/contact details, emergency contacts, school information, education level, documents and optional account credentials.
- Tutor profile: contact details, qualifications, teaching subjects/levels, professional experience, supporting documents and availability.
- Subject catalog by education level (e.g., elementary, middle school, high school, university).
- Complete academic history grouped by school year/semester/period while maintaining chronological history.
- Structured diagnostic and ongoing evaluations with templates, grading criteria, scores and tutor feedback; independent tutor contributions roll up into a unified student history by subject/period.
- Structured learning plans with objectives, milestones, due dates, activities and progress visible to students/guardians.
- Assignments: tutors provide instructions/deadlines, students upload work, tutors grade and provide feedback, guardians track completion.
- Progress dashboards with grade trends, attendance, completed objectives and learning goals.
- Weekly/monthly/on-demand academic progress reports prepared by tutors **require admin approval before family visibility**.
- Internal notes stay private; parent-facing attendance, assignments, grades and approved reports are visible according to permissions.
- File attachments to student records, lessons, assignments, courses and reusable materials.

## 5. Document and resource management

- Shared searchable academic library organized by folders, subject, education level, categories, document type and tags; reuse resources in courses/classes.
- Accept PDF, images, Word, Excel and PowerPoint; previews where supported.
- S3-compatible object storage, PostgreSQL file metadata, presigned uploads/downloads; scan uploaded files for malware before release.
- File-level visibility: private, tutor-only, or shared with authorized students/parents subject to role checks.
- Storage caps: global allowance, per-user/library quotas, per-file size limits.
- Soft-delete files to a recoverable recycle bin for configurable retention; authorized restores and eventual permanent deletion.

## 6. Billing, pricing and payments

### Pricing and products
- Service catalog pricing by subject, education level, duration and class type.
- Individual sessions, group per-student/per-session/full-course pricing, structured course fees, hour-based or session-based prepaid packages, and recurring weekly/biweekly/monthly tuition plans.
- Packages have configurable expiration and remaining credit balances. No automatic sharing or transferring of family credits was selected.
- Reusable discounts, scholarships, sibling discounts, promotions, eligibility criteria and expiration dates. Custom student/family pricing supported as selected in broad scope.
- Prices have effective dates; existing charges retain their historical price.

### Billing workflow
- Charges generated automatically from billable lessons, package purchases, course enrollment and recurring tuition.
- Family-consolidated billing and student-level charge breakdowns.
- Admins record cash/transfer/other external payments with date, amount and method. Partial payments and allocations across multiple charges/children are supported.
- Charges have service- or plan-based due dates, with family overrides. Track pending, partially paid and overdue amounts; in-app reminders to families.
- Downloadable branded PDF receipts, but not statutory Mexican electronic invoices.
- Admin-managed withdrawals: calculate unused sessions/hours and apply policy-based refunds or account credits.
- Refunds, reversals and corrections preserve source transactions in the audit trail; no silent overwriting of financial records.
- Automatic reconciliation for schedule changes; financial mutations must be transactional and idempotent.

## 7. Tutor compensation and payroll

- Flexible payment models: per-hour, per-session, per-course, fixed periodic salary, or salary plus additional paid hours/sessions.
- Tutor-specific rates for individual, group and structured courses; rate history with effective dates.
- Individual payroll frequencies: weekly, biweekly or monthly.
- Configurable payments for late cancellations, no-shows and makeup classes.
- Actual hours entered by tutors are submitted for admin approval. Academic Coordinators verify hours, Finance Staff prepare payroll, Owner or authorized Administrator approves final totals.
- Fixed salary may be prorated for partial pay periods, according to configurable rules and finance review.
- Configurable manual or recurring bonuses/deductions with types, descriptions and effective dates.
- Finalized payroll is locked; later corrections become traceable adjustments in future periods.
- Payments processed outside the platform and logged with payment date, method, reference and proof.
- Tutor portal shows approved earnings, sessions, hours, rates, bonuses, deductions, payment dates and downloadable payslips.

## 8. Financial operations and reporting

- Operating expense records: rent, utilities, supplies, equipment, maintenance and administrator-defined expense categories; categories can be created, renamed and deactivated without corrupting historical reporting.
- One-time expenses and auto-generated weekly/monthly/annual recurring expense templates.
- Track vendors, due dates, paid/unpaid status, payment method, reference number and attached receipt.
- Cash-basis and accrual-basis financial reporting; distinguish receivables and outstanding payables.
- Center-wide profitability, plus revenue, direct tutor costs and gross profit by individual tutoring, group tutoring and courses. Shared-overhead allocations to detailed course-level net profit were not selected.
- Analytics: revenue trends, utilization, student retention, course profitability, tutor performance, unpaid balances, payroll and other KPIs.
- Role-based customizable dashboards: drag/reorder widgets, select metrics, save preferences.
- PDF, CSV and Excel report exports, configurable columns, grouping, date ranges and saved report setups.

## 9. Operations, notifications and governance

- In-app notification preferences and read/unread indicators; schedule changes, assignments, payments, requests and approvals. No student/family email/WhatsApp/direct messaging.
- Central administrative approval inbox with assignees, statuses and priorities.
- Audit trail for important records: actor, action, timestamp, prior and new values; soft deletion/recovery for eligible records, financial corrections via adjustments/reversals.
- Owner/Admin/Coordinator/Finance functions aligned with predefined permissions; record-level access enforced on the server.
- Registration: admin invitation or self-registration request with administrator approval; enrollment itself **always administrator-controlled**.
- CSV/Excel imports and exports for students, tutors, schedules and financial data with validation.
- Central business settings: brand, operating hours, academic periods, pricing defaults, payment methods, scheduling policies, receipt templates.
- Privacy: guardian consent records, privacy-notice acceptance, controlled minors' data access and configurable academic/financial/document/audit retention subject to legal requirements.
- Admin error diagnostics and monitoring; failed background tasks visible with retry controls.

## 10. System architecture decisions

### Application stack
| Concern | Selected implementation |
|---|---|
| Frontend | React + TypeScript + Vite |
| UI | Tailwind CSS + shadcn/ui; design tokens; Storybook |
| Routing | React Router, guarded views |
| State/data | TanStack Query (API/cache) + Zustand (UI state) |
| Forms | React Hook Form + Zod |
| i18n | react-i18next, Spanish and English |
| Dates | date-fns + date-fns-tz |
| Calendar | FullCalendar; some resource views may require license |
| Analytics | Recharts + TanStack Table |
| Backend | FastAPI with domain-based modular monolith |
| API contracts | OpenAPI generated by FastAPI → generated typed TypeScript API client |
| Validation | Operation-specific Pydantic create/update/detail/list schemas |
| API errors | RFC 9457 Problem Details, codes, field errors, request IDs |
| API versioning | `/api/v1/...` |
| DB | PostgreSQL (self-hosted) |
| ORM | Async SQLAlchemy 2.0 with asyncpg |
| Connection pool | SQLAlchemy + PgBouncer |
| Schema migrations | Alembic; migration-first and CI validation |
| Keys/time | UUID primary keys; TIMESTAMPTZ UTC; ISO 8601 API |
| Pagination | Offset for ordinary tables; cursor for large histories |
| Search | PostgreSQL indexes, full-text search and trigrams |
| Architecture patterns | Domain modules; Unit of Work; application-level cross-module orchestration |
| Concurrency | Optimistic version checks; transactional constraints for critical operations |
| Financial operations | PostgreSQL transactions and idempotency keys |
| Background work | Procrastinate on PostgreSQL; dedicated worker container; periodic scheduler |
| Session store | PostgreSQL (Redis removed) |
| Caching | TanStack Query; performant PostgreSQL queries; no Redis backend cache |
| Rate limits | Caddy/FastAPI enforcement, without Redis |
| Notifications | PostgreSQL persistence with SSE and LISTEN/NOTIFY where appropriate |
| Reports | WeasyPrint PDFs; openpyxl XLSX; background generation via worker |
| Files | S3-compatible API, production S3, presigned URLs, malware scanning |
| Configuration | .env locally; GitHub Secrets delivered via SSM to EC2; pydantic-settings validation |
| Tooling | uv, Ruff, mypy; pnpm, ESLint, Prettier, TypeScript strict |
| Local commands | Makefile + Docker Compose (`make dev/test/lint/migrate/seed`) |
| Seed/test data | Idempotent reference seeds; Factory Boy and Faker demo/test factories |

**Redis and Celery are explicitly excluded.** Earlier choices for Redis-backed sessions, caching, rate limiting and Celery workers are superseded. PostgreSQL-based solutions replace those functions. Be careful implementing Redis-free rate limiting across more than one API process: it will need an appropriate shared store or enforced edge/proxy policy if scaled horizontally.

### Deployment and infrastructure
- **One AWS account; separate DEV, PROD and monitoring infrastructure.** Both DEV and PROD have a single independent EC2 instance, initially targeted at micro size, with configurable instance types. One extra EC2 instance hosts observability.
- **Docker Compose** locally and on EC2. Distinct Compose stacks for stateful PostgreSQL and application services. Docker `unless-stopped` restart policies plus systemd-managed startup and dependencies.
- **Dedicated encrypted gp3 EBS data volume per environment** for PostgreSQL, provisioned and sized via Terraform; reattachable upon instance replacement. Database filesystem mount/configuration must not accidentally format existing volumes.
- **Backups:** pgBackRest to S3, WAL archiving and PITR. Backups and recovery procedures must be exercised.
- **Images:** Amazon ECR for Docker images, promoted by immutable digest DEV → PROD. Git SHA and version tag such as `v1.2.0-20261016`. ECR lifecycle cleanup retains deployed digests and rollback candidates (ensure retention safety explicitly in CI/policy).
- **Network/HTTPS:** Public EC2 with Elastic IP and Route 53 origin hostname; security groups limit web origin traffic to CloudFront origin-facing ranges; no public SSH/PostgreSQL/PgBouncer endpoints. Route 53 customer hostnames → CloudFront → HTTPS Caddy origin on EC2 → web/API containers. TLS validated end-to-end; origin restriction to CloudFront IPs alone does not uniquely authorize this distribution.
- **EC2 management:** AWS SSM Session Manager, no inbound SSH; SSM Run Command to trigger Compose deployments; Terraform + cloud-init + SSM provision and manage instances.
- **IaC:** Terraform with separate environment state, encrypted/versioned S3 remote backend and native state locking. PR checks and plans; DEV applies on merge to `develop`; PROD requires GitHub approval; destructive plans require approval even in DEV; `prevent_destroy` on critical stateful resources.
- **CI/CD:** GitHub Actions OIDC with least-privilege DEV/PROD roles; feature branches → `develop` (auto DEV); tested changes → `main` (manual PROD approval); run tests, security checks, migrations as controlled deployment step, use health-checked rollouts and restore prior image on failure.
- **Migrations:** Alembic applied before new app version, with expand-and-contract/backward compatibility for image rollback; never automatically reverse DB migrations on app rollback.
- **Scaling:** EC2 instance and EBS volume sizes independently adjustable via Terraform; resizing may require downtime.

### Observability
- Full self-hosted stack on **dedicated monitoring EC2**: OpenTelemetry Collector, Grafana, Loki, Prometheus and Tempo.
- DEV and PROD forward structured logs, metrics and traces to the monitoring host; Grafana served over authenticated HTTPS; ingestion endpoints accessible only to authorized instances.
- Configurable retention by telemetry type and storage; Grafana dashboard alerts for CPU, RAM, disk and health; **no email or external incident notifications**.
- Distinguish app liveness (`/health/live`) from readiness (`/health/ready`), including PostgreSQL readiness check.
- Separate error tracking with Sentry was chosen early. Its hosting/integration arrangement remains to be finalized alongside the self-hosted observability setup.

### Quality and security
- pytest unit/integration tests, Vitest component tests and Playwright E2E tests; Testcontainers for isolated PostgreSQL-backed integration tests. CI runs E2E app with seeded fixtures.
- Dependency scanning, secret detection, static security analysis, image scans and automated authorization tests.
- Secure server sessions in HTTP-only cookies; CSRF protections; session expiration and revocation; password reauthentication for sensitive actions. Application enforces record ownership beyond role checks.
- WCAG 2.2 AA accessibility target; keyboard and screen reader support, contrast/focus/error treatment and automated accessibility tests.
- Frontend: centralized errors and safe retries; skeleton states and loading indicators; no unapproved optimistic financial mutations.
- Common responsive design adapted by role: desktop-oriented admin tools, mobile-friendly tutor/student/guardian interfaces. Light/dark/system themes and database-backed user preferences.
- UI component catalog in Storybook; centrally defined design tokens.
- Architecture documented in Markdown with diagrams and ADRs.

## 11. Canonical monorepo layout

The repository structure below is **identical to the one in `Classora_FULLREADME.md` (Part 3, §11)**. The playbook is the canonical reference for folder organization; future changes to this tree must be reflected in both documents.

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

- `_docs/` is the documentation directory; do **not** create a parallel `docs/` tree.
- `apps/web/` holds the React application, organized by business feature; `apps/api/` holds the FastAPI modular monolith and the code shared by the separate Procrastinate worker container.
- `packages/api-client/` contains the TypeScript client generated from the OpenAPI contract.
- `infrastructure/observability/` contains the dedicated monitoring stack; `infrastructure/terraform/` manages independently sized DEV, PROD, and monitoring infrastructure.
- `e2e/` holds cross-application Playwright tests; `AGENTS.md` and `CLAUDE.md` provide coding-agent entry points.
- `_docs/team/`, `_docs/process.md`, and `_docs/task-template.md` support the AI-assisted delivery workflow. These are **process documents**, not substitutes for the product requirements in this file.

## 12. Core database domain inventory (design starting point)

The detailed schema is **not designed yet**. Candidate entities:

| Domain | Candidate tables/entities |
|---|---|
| Identity | User, Role, Permission, UserRole, Session, Preference |
| Families | Student, Guardian, StudentGuardian, EmergencyContact |
| Staff | Tutor, TutorQualification, TutorAvailability, TutorTimeOff |
| Academic catalog | Subject, EducationLevel, AcademicPeriod |
| Scheduling | Classroom, Session, RecurrenceRule, Enrollment, SessionAttendance, Substitution |
| Courses | CourseTemplate, CourseModule, Cohort, CohortEnrollment |
| Academics | Evaluation, EvaluationTemplate, LearningPlan, Milestone, Assignment, Submission, SessionSummary, ProgressReport |
| Billing | ServicePrice, PriceVersion, Charge, Payment, PaymentAllocation, TuitionPlan, Package, CreditTransaction, Discount, Refund |
| Payroll | CompensationRate, TeachingHours, PayrollPeriod, PayrollLine, PayrollAdjustment, PayrollPayment |
| Finance | Expense, ExpenseCategory, ExpensePayment, ExpenseTemplate |
| Documents | File, Folder, FileAccessGrant, ResourceTag |
| Administration | ApprovalRequest, Notification, AuditEvent, BusinessSetting, ConsentRecord, ImportJob |

**Schema design requirements:** normalized relational relationships, appropriate foreign keys/uniqueness/check constraints, UUID primary keys, indexed search, optimistic concurrency on edited records, financial ledger/adjustment semantics, audit history, file soft-deletion, and historical rate integrity. These are candidates, not final table names.

## 13. Suggested build order (all within the intended full first release)

1. **Foundation:** Monorepo, Docker Compose, PostgreSQL, auth/RBAC, Terraform, DEV and PROD pipeline.
2. **Operational core:** Profiles, family associations, tutors, availability, rooms, calendar, scheduling, enrollments and courses.
3. **Academic workflows:** Attendance, session completion, evaluations, learning plans, assignments, document library and progress reports.
4. **Financial workflows:** Pricing, packages, recurring charges, payments, credits, payroll, expenses and reconciliation.
5. **Governance and reporting:** Approval inbox, audit trail, notifications, analytics, exports, configurable settings.
6. **Production hardening:** Load/performance assessment, security checks, backup-restore tests, accessibility and operational runbooks.

## 14. Reconciled decisions and remaining engineering validation

- **DEV is deployed**: An earlier two-environment local+PROD choice was replaced by **local, deployed DEV, deployed PROD**, plus a monitoring host.
- **No RDS or Fargate for the database**: PostgreSQL lives on each environment's EC2 with EBS. Fargate is not part of the selected deployment.
- **No Redis/Celery**: Procrastinate with PostgreSQL provides background jobs; sessions live in PostgreSQL. Revisit how fast rate limiting, SSE fan-out and caching behave at higher scale.
- **Micro is a target, not a guarantee**: Benchmark RAM and CPU usage, especially PostgreSQL, worker, PgBouncer, React serving and Docker overhead. Dedicated monitoring reduces but does not eliminate app-host pressure.
- **Health-checked rollout on a single EC2**: Automatic rollback and truly interruption-free deployment are different. Compose on a single small host may incur short downtime unless explicitly engineered to run overlapping versions within resource limits.
- **Public origin security**: Restrict CloudFront-to-origin connectivity, ensure HTTPS certificate renewal works, and consider authenticating the specific distribution rather than only AWS CloudFront IP ranges.
- **Secrets handling**: GitHub Secrets → SSM injection was selected; implementation must avoid printing secrets in CI/SSM logs or placing them in Docker images. Secrets stored on instances require restrictive permissions.
- **Data retention vs financial immutability**: Retention/deletion processes must preserve records required by law and the audit/ledger model.
- **User-visible email isn't in scope**: Password recovery/account activation will still need a secure delivery mechanism, which remains an integration detail to choose. Infrastructure alerts are dashboard-only by decision.
- **Sentry hosting** and specifics of self-hosted telemetry ingestion/retention need integration validation.
- **Detailed database schema, exact API contracts, UX wireframes, and user stories** are the next deliverables; this is the accepted requirements/architecture baseline, not implementation-ready DDL.


## 15. Core workflows and verifiable acceptance scenarios

The scenarios below translate the approved capabilities into checkable end-to-end behavior. They are **acceptance test starting points**, not a claim that the application is implemented.

### W1. Tutor assignment and class scheduling

1. An administrator selects an individual, group or course session, a student/cohort, subject, date/time and classroom if applicable.
2. The backend checks student, tutor, room, approved qualification, availability, enrollment/capacity and relevant policies. A conflicting booking must not be silently created.
3. The administrator selects an eligible tutor; the assignment is offered to that tutor for acceptance or decline.
4. An accepted assignment appears on authorized student, guardian and tutor calendars. A declined assignment returns to an actionable administrative state.
5. A recurring series can be modified for a single occurrence or future occurrences without rewriting already completed session history.
6. Overrides that the policy permits require authorization, a reason and an auditable record.

### W2. Tutoring request from a student or parent

1. A linked guardian or authenticated student requests an individual session using the available slots UI.
2. The request is **pending**, not an enrollment or confirmed booking; it does not create confirmed calendar occupancy by itself.
3. An administrator reviews the request, chooses a qualifying tutor and confirms or rejects it.
4. The requestor sees the final status in the application. Access controls prevent requests for unlinked children.

### W3. Session closeout to payroll

1. A tutor records attendance, actual hours and a lesson summary for an assigned session.
2. Submission creates a pending approval item; no unapproved hours enter finalized payroll.
3. The authorized academic reviewer approves or requests correction.
4. Approved hours enter the relevant tutor payroll calculation using **the effective compensation rules for that service date**.
5. Once payroll is approved/finalized, prior entries remain traceable; later corrections produce a new adjustment rather than silently changing a finalized period.

### W4. Billing, payments and family accounting

1. Enrollment, a billable class, package purchase or recurring tuition event creates the correct charge only once, even if a background job is retried.
2. Charges retain the applicable historical price and discounts even when the price catalog later changes.
3. Finance staff record an external payment, optionally partial, and allocate it across charges for one or more linked children in a family.
4. Outstanding balances equal posted charges/adjustments less valid allocations and refunds; duplicate payment submissions cannot silently double-post.
5. Changes to classes or enrollment generate policy-appropriate credits or adjustments; posted transactions are reversed/adjusted, never quietly rewritten.
6. Families see only their authorized charges, payment history, receipts and balances.

### W5. Academic record visibility

1. A tutor posts an internal session note and optionally publishes parent-facing learning information.
2. An administrator approves a progress report before its visibility changes to approved-for-family.
3. Parents can access approved reports, permitted grades and assignments for linked children only—not confidential internal notes or other families' data.
4. Tutor access to historical records follows the approved current-assignment and read-only former-student rules.

### W6. File submission and release

1. Authorized users request a presigned upload, constrained by type, size, quota and record access.
2. File metadata begins in a pending/unreleased state until server-side validation and malware scanning succeeds.
3. Only approved viewers obtain download access. Unauthorized URLs/requests cannot expose another family's or tutor's files.
4. Deletion moves eligible files into recoverable storage for the configured retention period; permanent deletion follows policy.

### W7. Background jobs and operational recovery

1. The FastAPI API and Procrastinate worker run as **separate containers**, sharing the same backend code and PostgreSQL environment.
2. Periodic billing and reminder tasks enqueue repeatably without creating duplicate business transactions.
3. Jobs persist across process restarts according to the PostgreSQL-backed queue; failed jobs expose status and support permitted retry.
4. Tests validate retries, idempotency and concurrent processing for financial operations.

### W8. CI/CD and rollback

1. Every pull request runs the required lint, types, tests, security scans and infrastructure checks relevant to changed files.
2. Merge to `develop` deploys to **DEV** automatically after checks; production does not deploy from that event.
3. A release promoted to **PROD** uses the same ECR **immutable digest** validated in DEV and requires the approved GitHub workflow.
4. A failing readiness check stops the rollout and reverts the application image when feasible; migrations are **not** automatically reversed.
5. Database backup-and-restore tests and PITR drills must demonstrate recoverability before production reliance.

## 16. Cross-cutting data and integrity requirements

- **Money:** Financial calculations must use exact decimal-safe representations; do not use binary floating point for charges, allocations, compensation or balances. The exact storage/value-object strategy remains a schema-level implementation detail, not a recorded choice.
- **Time:** Store instants as UTC-aware timestamps; capture the center's configured time zone for scheduling, local date boundaries, recurrence and due dates. Never infer class dates solely from UTC dates.
- **Identifiers and relations:** UUID keys, unique/check constraints and foreign keys; represent guardian-child links and tutor qualifications explicitly.
- **Permissions:** Server-side role + record-level checks in API endpoints, generated files, background jobs and presigned links. Never rely on frontend route guards alone.
- **Historical truth:** Use effective-dated catalog values, immutable posted financial activity, payroll locks, audit events and correction entries so past reports remain explainable.
- **Consistency:** Use transactions/idempotency for charge generation, payment allocations, payroll approvals and asynchronous retries. Database constraints—not frontend checks alone—protect business invariants.
- **Search and performance:** Index predictable access paths (user, family, schedule, billing status, academic period), test query performance, and cap export sizes or run exports asynchronously.
- **Privacy:** Retention and deletion must respect required financial/audit records while minimizing exposure of minors' personal and educational data.

## 17. Scope traceability and delivery artifacts

The following artifacts should derive from this specification; they **must not silently override it**:

| Artifact | Purpose | Owner / governance |
|---|---|---|
| `_docs/spec.md` | Product requirements and accepted technical constraints | Change only after a deliberate scope/architecture decision |
| `_docs/architecture.md` | Detailed components, deployment diagram and ADR references | Document tradeoffs and implementation consequences |
| `_docs/data-model.md` | Final entities, FK relationships, invariants, indexes and migration strategy | Schema review before implementing financial workflows |
| `openapi.yaml` or generated OpenAPI | Versioned frontend/backend contract; generate typed client | Endpoint contract tests and CI drift detection |
| `_docs/design-system.md` | Role-adaptive layouts, tokens, accessibility, components | UI review and consistent implementations |
| `_docs/testing-guidelines.md` | Test fixtures, permission matrix, E2E and financial invariants | QA and CI checks |
| `_docs/process.md` | GitHub Issues, grooming, implementation, QA and release steps | Separate development-playbook concern |
| `AGENTS.md` | Concise coding-agent project entry point | Links to spec and task-relevant guidance |

**Definition of done for an issue:** Checkable acceptance criteria pass; corresponding tests exist; affected permissions, financial invariants and audit trails are exercised; generated API contract stays in sync; migrations are safe; user-facing states and errors are handled; required documentation is updated. The workflow guide expands on the PM → Engineer → QA sequence.

## 18. Clarifications deliberately left open

These are **not permission to change the product scope**. They need explicit implementation decisions or verification before their related work is marked complete:

1. Exact PostgreSQL table names, relationship cardinalities, finance ledger conventions, monetary rounding approach and storage precision.
2. Exact authentication account-recovery delivery mechanism without introducing general outbound email notifications.
3. Whether/how Sentry is hosted or consolidated with the selected monitoring stack.
4. Details of rate-limit enforcement without Redis, and operation of SSE/`LISTEN/NOTIFY` in the chosen container process topology.
5. Concrete workflow for malware scanning, preview generation and safe presigned-download authorization.
6. FullCalendar licensing implications if premium scheduler/resource features are required.
7. Resource sizing and deploy interruption tolerance on a single small EC2 host; load testing and recovery time objectives.
8. Distribution-specific CloudFront origin authentication and secret exposure controls during SSM-based deployments.
9. Compliance/legal retention and guardian consent wording for the actual operating jurisdiction.

---

*Classora specification v1.2. Product scope reflects the prior approved functional decisions; additional workflows are derived testable interpretations. No application code or production infrastructure is claimed to have been deployed.*
