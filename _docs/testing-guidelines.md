# Classora testing guidelines

The rules every task follows when it adds or changes tests, and that QA checks
before a PASS (spec §10 Quality, §17). This document describes what exists
today. Where a later task adds something, the section names the owning task;
it does not decide anything that task or its ADR owns.

## Test layers

| Layer | What belongs here | Where the files live | Marker or project | Local command | CI job |
|---|---|---|---|---|---|
| API unit | Pure logic: validation, services with fakes, configuration, error mapping, job rules. No Docker, no network, no database. | `apps/api/tests/unit/` | `unit` (applied by directory) | `make test-unit` | `api-test` |
| API integration | Anything that needs PostgreSQL: repositories, the Unit of Work, endpoints through the ASGI app, seeds, jobs, PgBouncer behavior, authorization and financial invariants. Testcontainers `postgres:18`, plus the PgBouncer image built from `infrastructure/docker/pgbouncer` when a test asks for it. | `apps/api/tests/integration/` | `integration` (applied by directory) | `make test-integration` | `api-test` |
| Migration checks | Upgrade from an empty database, a single Alembic head, the downgrade/upgrade round trip, no drift between models and migrations. A subset of the integration layer. | `apps/api/tests/integration/test_migrations.py` | `integration` | `make test-migrations` | `api-test` |
| Web unit and component | Hooks, stores, formatters, the API call wrapper, routing, rendered components in jsdom, translation catalogs. | `apps/web/src/**/*.test.{ts,tsx}`, `packages/api-client/src/**/*.test.ts` | Vitest project `unit` (web); the api-client package's own Vitest run | `make test-web` | `web` |
| Storybook story tests | Every component story with its play function and an axe check, in light and dark, in headless Chromium. | `apps/web/src/**/*.stories.tsx` | Vitest projects `storybook-light`, `storybook-dark` | `make test-storybook` | `web-storybook` |
| E2E | Cross-application flows in a real browser against the full Compose stack (web → API → PgBouncer → PostgreSQL, worker running), plus the axe scan of each route. | `e2e/tests/` | Playwright project `chromium` | `make e2e` | `e2e` |

`make test` runs the API (unit and integration) and the web unit tests. It
does not run the Storybook story tests or E2E.

### Choosing a layer

- Test a rule at the lowest layer that can prove it. A business rule,
  a validation, an authorization decision or a financial invariant is tested
  in the API (unit or integration), never only through E2E.
- Authorization (role and record level) is always tested against the API with
  a real database: a hidden UI control proves nothing (spec §16).
- E2E covers flows that cross the browser, the web app and the API: a user
  completes a workflow, the screens show the server's result. Each E2E test
  replaces many manual clicks, not an API test.
- A component's states (loading, empty, error, disabled) and its accessibility
  are covered by stories, not by E2E.
- Route guards and hidden navigation items are tested in Vitest (test-only
  routes and items). They are UX only and never replace the API permission
  matrix.

## Fixtures and test data

### Integration isolation (M1-07)

- One Testcontainers PostgreSQL per pytest session, started only when an
  integration test needs it, on a random host port. It never uses the Compose
  `db` or its volume.
- A test that writes data or runs migrations asks for `fresh_database_url` (an
  empty database created for that test and dropped afterwards) or
  `tables_url` (the same, with the test-only tables). Tests share no data and
  pass in any order. Only read-only checks use the container's default
  database.
- Tests that need the pooler ask for `pgbouncer_url` (transaction mode, small
  server pool, built from the committed image and config).
- Writes go through the Unit of Work (`app.core.uow.UnitOfWork`). Tests never
  call `commit()` themselves and never commit outside a Unit of Work; a test
  that checks rollback lets the Unit of Work roll back.
- `tests/conftest.py` removes every variable `Settings` reads from the
  environment before each test. A test sets exactly the configuration it needs
  (`app_env`, `settings_for(...)`).

### Factories (M1-17)

- Every model factory extends `ClassoraFactory` (`apps/api/tests/factories/base.py`).
- Persist with `await persist(session, Factory)` or
  `await persist_batch(session, Factory, n)`. They flush and never commit.
  `Factory.create()` and `Factory()` raise; `Factory.build()` builds without
  the database.
- Data is reproducible: `reseed(n)` seeds Factory Boy and Faker, and an
  autouse fixture reseeds before every test. Never depend on a value relative
  to "today"; use fixed date ranges.
- Faker uses the `es_MX` locale. Emails come only from `reserved_email()`
  (`example.com`, `example.org`, `example.net`), never from Faker's email
  providers.
- Users come from `UserFactory` / `persist_user(session, roles=[...],
  delegations=[...])` (`apps/api/tests/factories/identity.py`) on a database
  with the reference seeds applied (`seeded_url`). Every factory user shares one
  precomputed Argon2id hash of the test-only `TEST_USER_PASSWORD`.
- Types follow ADR-0004: money is a `Decimal` with exactly 2 places
  (`money()`), never built from a `float`; calendar values are `date`
  (`calendar_date()`); instants are timezone-aware UTC `datetime` (`instant()`).

### Reference seeds and demo data

| | Reference seeds | Demo data |
|---|---|---|
| Purpose | Data every environment needs (for example roles, education levels) | Synthetic records for local, DEV and E2E use |
| Values | Fixed literals; no Faker, randomness, current date or environment-specific value | Factories with a fixed seed (`--seed`, default 1) |
| Idempotent | Yes (upsert on a key) | No (a rerun adds rows) |
| Where to register | Append to `REFERENCE_SEEDS` in `apps/api/app/seeds/registry.py`, in dependency order | Append a generator to `DEMO_GENERATORS` in `apps/api/tests/factories/demo.py` |
| Command | `make seed` (`python -m app.seeds`) | `make seed-demo` (`python -m tests.factories.demo`); refused unless `APP_ENV` is `local` or `dev` |
| Deletes rows | Never, with one exception: `identity.role_permissions` also deletes the pairs of the seeded roles that `ROLE_PERMISSIONS` no longer lists, so the database's role permission sets equal the code (ADR-0007). `roles` and `permissions` rows are never deleted. | Never |
| Never | Creates user accounts or credentials | Runs in PROD |

### Synthetic data only (architecture ISSUE-I7)

- All test, seed, demo and E2E data is synthetic. Never copy DEV or PROD data
  into a test, a fixture, a seed or a bug report.
- No real names, emails, phone numbers or identifiers (CURP, RFC, ...). A
  factory that adds phone numbers or national identifiers documents, next to
  the declaration, how its values are fictitious.

### Time

- A test whose result depends on the current date or time sets it explicitly
  (a fixed instant passed in, or fake timers). It never relies on the real
  clock or the host time zone.
- Such tests include at least one `America/Mexico_City` case (the center's
  default zone, ADR-0004). Where local dates, recurrence or due dates are
  involved, add cases at local midnight and around historic DST transition
  dates (Mexico City observed DST until October 2022) and for a zone that
  still has DST.
- The web unit tests run under `Pacific/Kiritimati` (UTC+14) by default, so a
  helper that leaks the host zone fails. E2E runs in `America/Mexico_City`.

### Time travel (M2-07)

- API code that needs "now" for a time-based rule that must be testable
  (session expiry, the session purge, and later rules like them) reads
  `app.core.clock.now()`, never `datetime.now()` or the database's `now()`.
- Tests move that clock with the `clock` fixture (`apps/api/tests/conftest.py`):
  it starts frozen at a fixed UTC instant and offers `clock.set(dt)` and
  `clock.advance(timedelta)`. It replaces a module attribute, so it also
  reaches the app inside `TestClient`, and it restores the real clock after
  the test.
- Never `sleep` to let time pass; test boundaries exactly (one second before
  and at the limit).

### Rate limits (M2-09)

- Login and reauthentication count attempts in `rate_limit_counters`
  (`app/core/ratelimit.py`). A per-test database starts empty. The autouse
  `empty_rate_limit_counters` fixture (`tests/integration/conftest.py`)
  empties the table before each test in every database shared by several
  tests (`module_seeded_url`, or a module fixture that calls
  `tests.integration.shared_databases.share_database`), and
  `reset_rate_limits(url)` empties it inside a test.
- Windows follow `app.core.clock.now()`: cross a window with the `clock`
  fixture (`clock.advance(timedelta(minutes=15))`), never by waiting.
- `TestClient` requests come from the peer `testclient`, which is not an IP
  address, so they all share one IP key (`unknown`): about 50 logins per test
  and database before `login.ip` answers `429`. A test that needs distinct
  client addresses builds its client with `TestClient(app, client=(ip, port))`.
- Never add a setting that disables the limits. A test that must log in more
  often resets the counters; E2E reuses signed-in sessions.

## Permission-matrix approach

These rules restate tasks §0.3, M2-03 and spec §10 ("automated authorization
tests") and §16. M2-03 built the framework (`apps/api/app/core/authz.py`,
`apps/api/tests/authz/`); `_docs/api.md` "Authentication and authorization"
describes the route declarations and the 401/403/404 rule.

- Every endpoint task adds at least one allowed test and one denied test.
- **The matrix** is `apps/api/tests/authz/matrix.toml` (read with the
  standard-library `tomllib`). One `[[route]]` table per method and path
  template, kept sorted by path, then method:

  ```toml
  [[route]]
  method = "GET"
  path = "/api/v1/users"
  access = "permission"            # or "public" (needs reason) / "authenticated"
  permission = "user.view"         # the code the route's require() names
  allow = ["owner", "administrator"]
  # optional: path_params = { user_id = "..." }, json = { ... }
  ```

  Every role not in `allow` is expected to get `403 permission_denied`;
  anonymous gets `401 authentication_required` for `authenticated` and
  `permission` entries. The file's header comment explains how to add an entry.
- **Static check** (unit, `tests/unit/test_permission_matrix_static.py`):
  every route of `build_app(include_dev_routes=True)` has exactly one entry
  and exactly one access declaration, and they agree; stale entries, unknown
  permissions or roles, a `public` entry without a reason and a protected
  route that does not document its `401`/`403` all fail, each listed as
  `METHOD path: reason`.
- **Runtime check** (integration, `tests/integration/test_permission_matrix.py`):
  one test per entry and actor (`METHOD path as role`): anonymous, each of the
  seven roles, and, for a delegable permission, an Administrator holding that
  delegation (no separate entry needed). Every `401`/`403` must be Problem
  Details with the expected `code`. Every actor is a real, active user in a
  migrated, seeded Testcontainers database (`matrix_user_for` in
  `tests/authz/harness.py`), and each case logs in through
  `POST /api/v1/auth/login` and sends that fresh session's cookie (M2-05).
  Only the runner's self-test on the test-only sample app overrides the
  session (`get_current_session`).
- **CSRF (M2-06):** test clients send the test app origin (`Origin`) by
  default and the matrix runner adds the session's `X-CSRF-Token` to unsafe
  requests, so CSRF never hides the outcome under test; the runtime check
  also generates `METHOD path csrf:no-origin` (expects `403 csrf_failed`) and
  `METHOD path csrf:no-token` (`403 csrf_failed` on a protected route, anything
  but `403` on a public one) for every unsafe entry. CSRF tests remove or
  change the header explicitly (`tests/authz/csrf.py`).
- **Reauthentication (M2-08):** a sensitive route's entry adds `reauth =
  "<action>"` (and `reauth_conditional = true` for `recent_auth_guard`); the
  static check requires it to match the route's marker, allows markers only on
  unsafe, non-public routes, and fails an unsafe route that requires a bound
  permission (`app/core/reauth.py`) without the marker. At runtime the allowed
  actors of an unconditional entry reauthenticate first (denied ones do not,
  so they still prove `permission_denied`), and `METHOD path reauth:none`
  expects `403 reauth_required`. The owning issue tests a conditional guard's
  two branches itself.
- **Rate limits (M2-09):** a route with `rate_limit(...)` lists the same codes
  in `rate_limit = [...]` (order ignored) and documents `429`; every unsafe
  `public()` route declares a policy or has `rate_limit_exempt = "<reason>"`;
  every policy is applied by a route or listed in `PENDING_POLICIES`
  (`app/core/ratelimit.py`), never both. Static check only: the runtime matrix
  sends no rate-limit cases (integration tests cover the behaviour).
- Unit tests of a route declaration use a test-only app and override
  `get_current_principal` with `app.dependency_overrides`.
- Record-level (IDOR) tests are separate from role checks: a user with the
  right role asks for another family's, tutor's or student's record. Use
  `assert_same_not_found(hidden_response, missing_response)` from
  `tests/authz/assertions.py`: both are `404 not_found` with identical bodies
  apart from `request_id` and the same headers.
- A denial returns `403` with Problem Details (`_docs/api.md`). A record that
  exists but is not visible to the caller returns `404`, the same as a record
  that does not exist. Record policies (`can_<action>_<resource>`) are
  unit-tested per role.
- Background jobs, generated files and presigned links are checked as well,
  not only endpoints (M10-01 sweeps them all); they use `ensure_permission`.

## Audit

M2-04 built the audit trail (`apps/api/app/core/audit.py`; architecture
ISSUE-A3). Events are written in the same transaction as the change.

- **Mark the model.** A model whose changes must be audited mixes in
  `Audited` and lists its sensitive columns: `__audit_exclude__` for columns
  that must never appear (password hashes, tokens, file contents) and
  `__audit_mask__` for columns stored only as `"[masked]"`. The unit guard
  (`tests/unit/test_audit_guard.py`) fails any audited model with an unlisted
  `password|token|secret|hash` or binary column.
- **Explicit events.** Actions that are not column changes use
  `record_audit_event(session, action="<module>.<event>", ...)`: login
  attempts, token issue and redemption, password changes and resets, and
  overrides with a reason. Never put a password, a token or a hash in
  `details` (a sensitive key is refused).
- **Assert the events.** Every issue that audits something asserts its events
  with `audit_events_for(session, record_type=..., record_id=..., action=...)`
  and `assert_no_secret_in_audit(session, *secrets)` from
  `apps/api/tests/audit/assertions.py`.
- **ORM only.** Never change an audited table with raw SQL or Core
  statements. ORM bulk `insert`/`update`/`delete` statements on an audited
  table raise `AuditBypassError`; raw `text()` SQL cannot be detected and is
  forbidden by this rule. Change rows with `session.add` / `session.delete`.
- **Rollback.** An audit row rolls back with its transaction. An event that
  must survive a failed request (for example a failed login) is written in a
  transaction that commits.
- `audit_events` is append-only: `UPDATE`, `DELETE` and `TRUNCATE` fail with
  SQLSTATE `42501` for every role.

## Financial invariant tests

M6-18 (billing) and M7-07 (payroll) own the suites; every financial task
follows these rules.

- `Decimal` only, never `float`, in code and in tests. Round half-up per line
  item (ADR-0004). Assert exact `Decimal` values, never approximate ones.
- The balance equation (W4.4): outstanding balance = posted charges and
  adjustments − valid allocations and refunds.
- A charge is created only once, even when the job that creates it is retried
  (W4.1, W7.2). Tests run the job twice and count rows.
- Posted records are corrected only through adjustments or reversals, never
  rewritten (W4.5). Tests check that the original row is unchanged.
- Duplicate or concurrent submissions never double-post (W4.4, W7.4). These
  tests use real concurrent sessions against the Testcontainers PostgreSQL,
  not mocks.
- Randomized operation sequences are reproducible: the test prints its seed
  and accepts it as input. A property-based testing library is **not**
  approved; adding one needs approval first.

## E2E data rules

- E2E runs only against the isolated E2E Compose project (`classora-e2e`,
  started by `make e2e`). It never runs against a developer's `make dev`
  database, DEV or PROD.
- Every run starts from an empty volume, then runs the migrations, then the
  reference seeds, then the demo command with the fixed seed (`--seed 1`).
- Tests do not depend on each other or on the order they run in, and they
  pass with Playwright's parallel workers. Data a test changes is created by
  that test, with a unique per-test suffix. Tests never depend on a
  Faker-generated value by position.
- Named fixtures that tests read (for example one account per role, from M2)
  are added by their domain task as deterministic demo generators with
  documented keys. The task that adds them also lists them in this section.
  Named E2E fixtures:
  - `demo.owner` (password `classora-demo-owner`): an active Owner, from the
    `identity.demo_owner` demo generator. It exists only in local stacks
    (`APP_ENV=local`, including the E2E stack); the E2E tests read it from
    `e2e/tests/support/credentials.ts`.
  - `e2e.coordinator`: the Academic coordinator that
    `e2e/tests/auth/invitations.spec.ts` invites and activates (#29). It is
    created by that test in every run (unique per run, because every run
    starts from an empty database); no other test may use the name.
  - `e2e.reset.tutor` and `e2e.change.guardian`: the Tutor whose password
    is reset and the Parent/Guardian who changes their own password in
    `e2e/tests/auth/password-reset.spec.ts` (#30). The same rules: created by
    that test in every run, no other test may use the names.
  - `e2e.parent`: the Parent/Guardian who requests an account on
    `/register` and is approved by the demo Owner in
    `e2e/tests/auth/registration.spec.ts` (#169). The same rules: created by
    that test in every run, no other test may use the name. The run's one IP
    sends 1 of the 10 registration requests `registration.ip` allows per hour
    (`REGISTRATIONS_PER_RUN` in `e2e/tests/support/auth.ts`).
  - `e2e.suspend.tutor`: the Tutor whom the demo Owner suspends and
    reactivates in `e2e/tests/users/suspend.spec.ts` (#31). The same rules:
    created by that test in every run, no other test may use the name.
- **Never change `demo.owner` in a spec** (its profile, state, roles or
  password): every test shares its signed-in session (`storageState`). A test
  that needs an Owner to act signs `demo.owner` in in its own fresh context
  and changes only the users it created.
- **`createActiveUser(playwright.request, baseURL, { username, role,
  password })`** (`e2e/tests/support/users.ts`, #30) creates an active Tutor
  or Parent/Guardian through the API in its own fresh API context: the demo
  Owner signs in and invites the user (no reauthentication for these roles),
  then the invitation is looked up and redeemed. It costs one login and two
  redemption calls; count them in the budgets below.
- Only synthetic data is used. Committed E2E credentials are placeholders that
  work only in the local or CI E2E stack.

## E2E authoring rules

- Use role-based or label-based locators first (`getByRole`, `getByLabel`).
  Use `data-testid` only where no accessible role or name is stable.
- Read UI text from the web app's translation catalogs
  (`e2e/tests/support/catalog.ts`) instead of repeating strings.
- Use web-first assertions (`await expect(locator).toBeVisible()`). No fixed
  sleeps (`waitForTimeout`).
- No retries: a test that fails once is a failure. `test.only` fails the run in
  CI (`forbidOnly`).
- The time zone (`America/Mexico_City`) and the locale (`es-MX`) are fixed in
  `e2e/playwright.config.ts`. M1-15 adds the per-language runs.
- Viewports: desktop Chromium only. A task that names "mobile viewport" adds a
  project.
- A new route gets an axe scan (next section).
- **Signed-in state (#28):** `e2e/global-setup.ts` signs the demo Owner in
  once through the API and saves Playwright `storageState` to
  `e2e/.auth/owner.json` (gitignored); every test starts signed in from it.
  The shared session is never logged out.
- **Fresh contexts for login and logout:** a test that logs in or out uses
  `test.use({ storageState: SIGNED_OUT_STATE })`
  (`e2e/tests/support/auth.ts`). A login sent with the shared cookie would
  revoke the shared session (#23 rotation), and a logout would end it.
- **Login budget:** the whole run shares one `login.ip` budget of 50 per 15
  minutes, and `demo.owner` has 10 failures per 15 minutes (#27). Each
  `make e2e` starts from an empty volume, so the budget is per run. Keep the
  count in `LOGIN_ATTEMPTS_PER_RUN` (`e2e/tests/support/auth.ts`) up to date
  and well under both limits; never add a setting that disables the limits.
  One-time links (invitations and password resets) share one
  `token_redemption.ip` budget of 20 lookups and redemptions per 15 minutes
  (#29, #30); keep `TOKEN_REDEMPTIONS_PER_RUN` up to date too.
- **Reauthentication dialog:** a test that needs the password dialog signs in
  with a fresh login in its own fresh context (#29 L1), never with the shared
  `storageState`: parallel tests share that session and its reauthentication
  mark.
- A failing run keeps the trace (`trace.zip`), a screenshot and the Compose
  logs in `e2e/test-results/`. Open a trace from the repository root with
  `corepack pnpm --filter @classora/e2e exec playwright show-trace test-results/<test>/trace.zip`
  (the command runs in `e2e/`, so the path is relative to `e2e/`).

## Accessibility tests

- Target: WCAG 2.2 AA (spec §10, `_docs/design-system.md`).
- Both layers run the WCAG 2.0, 2.1 and 2.2 level A and AA rules (axe tags
  `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`), in light and dark:
  Storybook for components (`make test-storybook`; these tags plus
  `best-practice`, set as `runOnly` in `apps/web/.storybook/preview.tsx`) and
  E2E for routes (`make e2e`; exactly these tags, through
  `@axe-core/playwright`). Selecting the tags also runs rules axe leaves off
  by default, such as `target-size` (WCAG 2.2). A scan passes only with zero
  violations.
- No rule is disabled globally.
- A per-test exclusion needs a reason comment in the test and a line in the
  list below (story-level exceptions are listed in `_docs/design-system.md`).
- The manual keyboard and screen-reader audit belongs to M10-03.

E2E axe exceptions: none.

## Quality practice

### New issue checklist

Spec §17 definition of done, mapped to the tests a change needs:

| Change | Tests it adds |
|---|---|
| Endpoint | Integration tests for success and Problem Details errors; one allowed and one denied test per role rule; an IDOR test for record access; a matrix entry in `tests/authz/matrix.toml`; `make check-api-client` in sync. |
| Financial change | The invariant tests above, including retry and concurrency cases, with exact `Decimal` assertions. |
| UI screen | Unit tests for its logic; stories for loading, empty and error states in light and dark (axe); es and en text; an E2E test when it completes a cross-application flow, with an axe scan of the route. |
| Migration | `make test-migrations` passes (upgrade, single head, round trip, no drift); the change is expand-and-contract compatible with the previous image. |
| Job | Idempotency (run twice), retry and failure behavior, and the record-level checks the job enforces. |

### Mutation check

QA proves that a new test can fail: break the behavior it covers (for example
remove the authorization check, change the rounding, drop an `alt`), run the
narrowest command that contains the test, record the failing output, then
restore the code and record the green run. Mutations are never committed.

### W1–W8 traceability

M10-06 fills in this section (one row per W1–W8 scenario and the tests that
cover it).
