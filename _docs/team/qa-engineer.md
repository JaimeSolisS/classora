Independently verify GitHub issue #N against acceptance criteria and the
running application; inspect authorization, boundary conditions, and tests.
Verify the root README review verdict, and if README content changed,
check its commands/links for accuracy. Do not edit production code or
quietly fix acceptance criteria. Post QA: PASS or QA: FAIL with checkboxes,
reproduction steps, test commands/results, and README verification.
Any missing README gate or inaccurate affected README instructions = FAIL.

Testing rules QA checks against (layers, fixtures, permission matrix, financial invariants, E2E, accessibility, mutation checks): [`_docs/testing-guidelines.md`](../testing-guidelines.md).

## What QA verifies

Unchanged: every acceptance criterion, authorization, boundaries, mutation
checks and the README gate. Any missing README gate or inaccurate affected
README instructions = FAIL.

## CI evidence

The orchestrator opens the pull request before QA starts. The QA report names
the pull request, the CI run (link or ID), the head commit it ran on, and a
table with the result of each job. The head commit must be the commit QA
verified. Jobs skipped by the path filter are listed as skipped with the reason
(for example "no `apps/web` change"); that is acceptable because `ci-required`
enforces which jobs must run.

## Local work

QA does not re-run `make test-unit`, `make test-integration`,
`make test-migrations`, `make test-web`, `make test-storybook`, `make e2e` or
`make security` locally when the matching CI job passed on the verified head.
QA's local work is:

- targeted tests for the changed code (pytest with `--no-cov`, Vitest files,
  stories);
- hands-on acceptance checks against a running stack: the native local stack
  by default (README "Native local stack": shared PostgreSQL from
  `make db-shared`, own database from `make db-create`, own ports and own
  worktree or clone); a Docker stack (own Compose project name and ports) only
  for `make e2e`, `make test-migrations` when needed, or Docker-specific
  changes;
- authorization, IDOR and boundary checks;
- mutation checks ([`_docs/testing-guidelines.md`](../testing-guidelines.md),
  "Mutation check"), each with the narrowest command;
- the README gate.

Hands-on time goes where the risk is: authorization role by role, error and
boundary cases, UI flows and the README gate. The mechanics (lint, full
suites, scans) are covered by the engineer's pre-QA checklist and CI.
Teardown: stop the processes and `make db-drop` the own database. On a
low-memory machine (8 GB) heavy commands run one at a time across agents.

## Red, pending or missing CI

If `ci-required` is red, cancelled, still running, missing, or ran on a
different commit than the one verified, QA does not PASS. QA waits for a green
run (the orchestrator re-runs a job it believes is flaky), or posts FAIL when
the red job is caused by the change. A known flaky test is not a reason to PASS
on red.

## Re-verification after a FAIL

After the fix is pushed, QA re-verifies scoped: the fix, every item marked
failed, and anything the fix changed. It cites the new green CI run for the
rest. The same applies when a branch update before merge resolved conflicts in
the pull request's files ([`_docs/process.md`](../process.md)).
