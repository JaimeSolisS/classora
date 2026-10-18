# ADR-0002: Module boundaries and the reporting module

## Status

Accepted

## Date

2026-10-18

## Sign-off

Owner: JaimeSolisS. Decision items D2 and D3 approved as recommended (option (a) for each) on 2026-10-18 (GitHub issue #19, M2-01, owner decision comment). No amendments.

## Context

Resolves architecture §10 **ISSUE-A1** (enforcing module boundaries) and **ISSUE-A2** (home for analytics, dashboards and exports) in [`../architecture.md`](../architecture.md) §10.2. Records decision items **D2** and **D3** of issue #19.

- Spec [`../spec.md`](../spec.md) §10 (application stack) names "Domain modules; Unit of Work; application-level cross-module orchestration". Architecture §4.1 lists twelve domain modules with a common internal layout, and §4.2 states the approved pattern: modules call each other only through `service.py`, multi-module changes live in `app/workflows/` in one Unit of Work, and there is no in-process event bus.
- The spec does not say whether a module may import another module's ORM models or join across module tables, and nothing enforced the boundary.
- Spec §8 asks for analytics, dashboards, saved reports and exports. Reports read across almost every module, and none of the twelve domains in spec §12 owns them.

## Decision

### Boundary rule (ISSUE-A1)

- Database **foreign keys** between tables of different modules are allowed.
- Code under `app/modules/<x>/` imports another module only through `app.modules.<y>.service` or `app.modules.<y>.schemas`. It never imports another module's `models`, `repository`, `router`, `permissions` or `tasks`.
- Modules never import `app.workflows`.
- Operations that change state in several modules live in `app/workflows/` and run in **one Unit of Work** (one database transaction).
- Reads that join tables of several modules are allowed only in `app/workflows/` and in the `reporting` module.
- `app/core/` is shared infrastructure and may be imported by anyone.

### Enforcement (D2 option (a))

- A pytest test in `apps/api/tests/unit/` parses every module's source with the standard-library `ast` module and fails on any forbidden import, naming the file, the line and the imported target.
- It runs in `make test-api`, and therefore in CI.
- It adds no dependency.
- Issue #20 (M2-02) adds the test together with the first module.
- A deliberate exception to the rule needs a new ADR.

### Reporting module (ISSUE-A2, D3 option (a))

- A read-only `reporting` module owns saved report setups and dashboard widget preferences.
- It runs read-only SQL across module tables and never writes to other modules' tables.
- Heavy exports run as worker jobs.
- It is built in M8, not in M2. Accepting it now settles the reporting exception in the boundary test up front.

## Consequences

- Module internals (models, repositories, routers, permissions, tasks) can change without breaking other modules, because only `service.py` and `schemas.py` are public.
- A breach of the rule fails `make test-api` locally and in CI instead of surfacing in review.
- Cross-module features need either a service call or a workflow. Cross-module joins outside `app/workflows/` and `reporting` are not allowed, so some reads take an extra service call.
- #20 (M2-02) gains the boundary test as scope (approved with D2). Every later module is checked by it from the start.
- The `reporting` module does not exist until M8. Until then, no module may host cross-module report queries.
- Unchanged by this ADR: ISSUE-A3 (audit capture) stays PROPOSED and is handled when #22 (M2-04) is groomed.

## Alternatives considered

- **`import-linter` as a dev dependency** with contracts in `pyproject.toml` (D2 (b)). Rejected: a new tool that needs approval, and the "only `service`/`schemas`" rule needs one contract per module.
- **Code review only** (D2 (c)). Rejected: nothing checks the rule automatically, so breaches show up late.
- **Leaving ISSUE-A2 PROPOSED until M8** (D3 (b)). Rejected: accepting the `reporting` module now is one paragraph and settles the exception in the boundary test up front.

## References

- [`../architecture.md`](../architecture.md) §4.1, §4.2, §10.2 ISSUE-A1 and ISSUE-A2, §11
- [`../spec.md`](../spec.md) §8 (reporting and analytics), §10 (application stack: domain modules, Unit of Work, cross-module orchestration), §12 (domain inventory)
- GitHub issue #19 (this decision gate, items D2 and D3); #20 (M2-02, adds the boundary test)
