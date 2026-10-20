Implement ONLY groomed GitHub issue #N. Respect its acceptance criteria,
\_docs/spec.md, \_docs/architecture.md, and the relevant API/data/docs rules.
Add backend/frontend/E2E tests and migration checks as appropriate.
Run tests, commit changes, and comment the exact changes and commands/results.
Review root README.md, update reader-facing content only if needed,
verify any changed commands/links, and post a README verdict.
DO NOT close the issue or claim independent QA PASS.

Testing rules (layers, fixtures, permission matrix, financial invariants, E2E, accessibility): [`_docs/testing-guidelines.md`](../testing-guidelines.md).

## Pre-QA checklist

Before handoff, run these and record each command with its result in the
engineer report. They catch the mechanical CI failures before a QA round.

- `make lint` (includes mypy and tsc).
- `make sast`.
- `make scan-secrets`.
- The tests for the changed area: targeted pytest with `--no-cov` (for example
  `cd apps/api && uv run pytest --no-cov tests/integration/test_x.py`),
  targeted Vitest files, and the Storybook stories that were added or changed.

Also required when they apply:

- An API route, schema or error code changed: `make check-api-client`.
- A migration was added or changed: `make test-migrations`.
- An E2E spec, the demo seed or the E2E stack changed: `make e2e` (the whole
  suite; there is no spec filter).

Full suites (`make test-unit`, `make test-integration`, `make test-web`,
`make test-storybook`, `make e2e` beyond the case above) run locally only when
the change is risky, for example shared fixtures, middleware or the
authorization layer. The report says which ran and why. Otherwise CI covers
them on the pull request.

Run heavy commands one at a time. `make sast` and `make scan-secrets` need
Docker.
