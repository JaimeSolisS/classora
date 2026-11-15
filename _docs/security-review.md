# Classora security review (M10-02, #150)

A targeted, evidence-based review of Classora's security posture before
go-live. It covers sessions, CSRF, headers and CSP, rate limits and client-IP
spoofing, the public edge and direct-host bypass, secrets, dependency posture
and authorization. Every finding has a severity and a status; no critical or
high finding is open.

## Scope

The nine areas of the groomed issue (#150 E1 (a), owner-delegated, pending
owner review):

1. Sessions: fixation, cookie attributes, token storage, timeouts, revocation,
   logout, reauthentication.
2. CSRF: `Origin` and token checks, CORS, state-changing `GET` routes.
3. Security headers and CSP, on the SPA and the API.
4. Rate limits and client-IP spoofing (the #27 authentication limits, the
   per-user domain limits, #163's general limit, the SSE cap, enumeration).
5. Edge bypass (Caddy on EC2, ADR-0010): direct IP, other SNI or `Host`,
   plain HTTP, other ports, `/health/*`. This replaces the issue's original
   "origin bypass of CloudFront": there is no CloudFront (#150 E2 (a)).
6. Secrets: git history, images, the DEV host's env file and containers, the
   deploy document, GitHub secrets, the OIDC trust, logs.
7. Dependency posture: lockfile audits, image scans, scan exceptions,
   Dependabot, runtime versions.
8. Authorization: the evidence of #149 (`idor.toml`) and #640 (`jobs.toml`),
   plus nested body ids reviewed by hand.
9. Availability notes (recorded only; #152 measures load).

### Not reviewed

- A third-party certification or penetration test; automated DAST scanners
  (#150 E1 (c)); fuzzing; destructive or high-volume testing (#152 owns load).
- Uploads, presigned URLs and file-type spoofing: deferred with the file
  system (owner, 2026-10-21).
- **PROD.** No request, AWS read or SSM command against PROD was intended in
  this run. PROD is argued from code (same module, Compose overlay and
  Caddyfile, area 5); PROD-only checks are owner actions for the #156 go-live
  checklist.
- A re-sweep of authorization: #149 and #640 are the evidence.
- Privacy and legal retention (#155, #519) and telemetry personal-data
  scrubbing (done in #612 and #617; cited only).
- The owner-accepted `style-src 'unsafe-inline'` (#24 E2).
- Repository settings (Dependabot alerts, branch rules): owner actions only.

## Method

- **Code and tests.** Each control was read in the code and tied to the tests
  that already prove it (pytest node ids). A test was added only where a fix
  needed one.
- **Scanners.** `make security` (`audit-deps`, `scan-secrets`, `sast`), the
  Trivy results of the latest CI `images` job and `deploy-dev` pre-push scan.
- **Scripts over the app.** The `GET` routes and their call graphs were
  walked for writes; the OpenAPI request bodies of the routes a tutor,
  student or guardian can call were walked for nested ids (area 8).
- **Live probes against DEV** (`https://dev.classora.dev`, the DEV Elastic
  IP and `grafana.classora.dev`), unauthenticated, non-destructive and low
  volume (about 750 requests, area 4 explains the overshoot). No DEV
  configuration change, restart or deploy.
- **Read-only AWS and GitHub reads** for DEV and monitoring only (profile
  `jsolisdev`): security groups, parameter names (never values), and one
  read-only SSM shell command on the DEV host (file mode, environment
  variable names, release).

The live checks lock the operator's IP out for a while: check 4a out of DEV
logins for up to 15 minutes, check 4b out of the DEV API for up to 60 s. Run
them last and never during someone else's DEV work.

## Environment

| Item | Value |
|---|---|
| Date | 2026-11-12 |
| Reviewed commit | `develop` at `4e962322` (#163 merged), branch `m10-02-security-review` |
| DEV release | `4e962322533ba05f21f83a5347572ff756814d86` (read from the host's `releases/current`) |
| Scanners (Makefile pins) | pip-audit 2.10.1; pnpm 12.14.0 `audit`; gitleaks v8.30.1; Semgrep 1.179.0; Trivy 0.75.0; actionlint 1.7.8 (images by digest) |
| Runtimes (ADR-0001) | Python 3.14, Node 24, PostgreSQL 18.6, Caddy 2.11.4, FastAPI 0.143.3, uvicorn 0.54.0, Starlette 1.8.0 |

## Severity scale

#150 E3 (a), owner-delegated:

| Severity | Meaning | Policy |
|---|---|---|
| Critical | Unauthenticated access to personal or financial data, authentication bypass, code execution, an exposed infrastructure secret | Fixed in this PR, or a blocking `security` bug issue (blocks #156) when the fix needs a migration, a new dependency or more than about half a day. |
| High | Cross-party access (IDOR), CSRF on a state-changing route, session fixation or hijack, a client-IP spoof that defeats a rate limit, any path to the API that skips Caddy | As critical; an accepted-risk ADR is allowed for high only, flagged for owner review. |
| Medium | A defense-in-depth gap with a plausible path | Fixed here if about 1 hour or less with no new dependency, else a follow-up issue. |
| Low / Info | Disclosure with no direct exploit, hygiene, or accepted design | Recorded; fixed if trivial, else accepted with a reason. |

Every fixed finding has a regression test that fails without the fix.

## Findings

No critical or high finding was found. Two mediums and two lows are fixed in
this PR; the rest are owner actions, one follow-up, or accepted.

| Id | Area | Title | Severity | Evidence | Status |
|---|---|---|---|---|---|
| SR-01 | 7 | No dependency monitoring between pull requests: Dependabot alerts are disabled and the security jobs ran only on pull requests and pushes, while ADR-0016 relied on the alerts | Medium | `gh api repos/<owner>/classora/dependabot/alerts` → 403 "Dependabot alerts are disabled for this repository"; ADR-0016 "There is no scheduled run" | Fixed in this PR (test: `apps/api/tests/unit/test_security_audit_workflow.py`; `.github/workflows/security-audit.yml`, ADR-0016 amendment) + owner action O1 |
| SR-02 | 3, 9 | No request-body size limit: FastAPI reads the whole body before any dependency (the `Origin` check, authentication), and neither Caddy, uvicorn nor the app capped it, so one unauthenticated `POST` of hundreds of MB could exhaust the API container's 512 MiB | Medium | FastAPI 0.143.3 `fastapi/routing.py`: `await request.body()` (line 458) before `solve_dependencies` (line 511); no `request_body` in the Caddyfile; no `--limit-*`/body cap in the uvicorn command. Not reproduced on DEV (destructive). | Fixed in this PR: Caddy `request_body max_size 4MB` streams the body and cuts it off at 4 MB (the API receives at most 4 MB, its answer is discarded, the client gets `413`) (test: `infrastructure/docker/edge/smoke.sh` "POST /api 5 MB body status" and "… gets no upstream answer") |
| SR-03 | 3 | Product disclosure: `server: uvicorn` and `via: 1.1 Caddy` on API answers, `server: Caddy` on the SPA | Low | Live: `GET /api/v1/auth/me` → `server: uvicorn`, `via: 1.1 Caddy`; `GET /` → `server: Caddy` | Fixed in this PR, Caddy's own error responses (405, 413, 404) included via `handle_errors` (tests: `tests/unit/test_api_image.py::test_the_api_command_sends_no_server_header`, `smoke.sh` "GET / has no Server header", "/api has no Server header …", "/api has no Via header", "the 413 (Caddy's error path) has no Server header", "the 405 …", "a missing asset (error path) …") |
| SR-04 | 6 | The DEV bootstrap parameter `/classora/dev/owner_initial_password` still exists after the first sign-in | Low | `aws ssm describe-parameters` (names only): present, last modified 2026-11-12 | Owner action O2 (and O3 for PROD, #156) |
| SR-05 | 8 | Family notifications (session moves, series edits, closure skips, substitutions) still went to a linked guardian whose profile is **deactivated**, who has lost access to the student; params are ids and instants only | Low | `families.service.student_notification_recipients` had no `Guardian.is_active` filter, unlike `families.facts` (#640 QA side note) | Fixed in this PR (test: `tests/integration/test_class_series_closure_changes.py::test_a_guardian_with_a_deactivated_profile_is_not_told`) |
| SR-06 | 7 | Images carry HIGH OS/Go advisories that have fixed versions: `zlib1g` (api, pgbouncer), `apt`/`libapt-pkg7.0` (pgbouncer), `go-jose/v4` in Caddy 2.11.4 (web). No CRITICAL. ADR-0016 fails only on fixed CRITICAL | Low | CI `images` job (run 43245977655) and `deploy-dev` pre-push scan (run 43266931107): api 109 HIGH, web 27 HIGH, pgbouncer 117 HIGH, 0 CRITICAL; 7 rows `fixed` | Follow-up #648 |
| SR-07 | 7 | `pnpm audit`: 2 moderate advisories, both in dev tooling (`glob` via Storybook's docgen plugin, `minimatch` via ESLint) | Info | `make audit-web`: "2 vulnerabilities found, Severity: 2 moderate" (GHSA-3g3m-2fjr-6pw4, GHSA-g3hv-75wg-m249) | Accepted: build-time dev tools, below the `high` threshold (ADR-0016 E4); the weekly audit (SR-01) keeps watching |
| SR-08 | 5 | A request with the right SNI and another `Host` gets an empty `200` from Caddy | Info | Live: `Host: evil.example` → `200`, 0 bytes, `server: Caddy`, no `x-request-id` (the API never saw it) | Accepted: Caddy's default for an unmatched host; nothing reaches the API |
| SR-09 | 3 | HSTS has no `includeSubDomains` or `preload` | Info | Live: `strict-transport-security: max-age=31536000` | Accepted: `.dev` is on the browsers' HSTS preload list as a whole TLD |
| SR-10 | 6 | Caddy's access log on the host keeps every request header (the `X-CSRF-Token` included) and the full URI; Caddy redacts only `Cookie`, `Set-Cookie` and `Authorization` | Info | `smoke.sh` edge logs; `infrastructure/observability/collector/agent.yaml` `transform/edge` | Accepted: the collector's allowlist drops headers, URIs and IPs before anything leaves the host (#617); Docker's logs are root-only and rotated; the CSRF token is useless without the `HttpOnly` cookie |
| SR-11 | 8 | Notifications are not re-filtered at read time; skip notices are also written for suspended users (#640) | Low | `administration/notifications.py` lists the recipient's rows as written | Accepted: recipients are computed at write time from current links (SR-05 closes the profile gap); params hold ids, instants and counts only; a suspended user cannot sign in |
| SR-12 | 9 | Receipt and payslip PDFs render in the API process (`anyio.to_thread.run_sync`, `app/core/pdf.py`), not the worker | Info | Code | Accepted: bounded by authentication, record policy, #163's 600/min per IP and SR-02's body cap; #152 measures the PDF scenario |
| SR-13 | 5 | `https://grafana.classora.dev/api/health` answers anonymously with Grafana's version | Info | Live: `200 {"database":"ok","version":"12.4.14",…}`; every other probed path needs a login (`/` → `302 /login`, `/api/dashboards/home` and `/api/datasources` → `401`) | Accepted: Grafana's built-in health endpoint, no data |
| SR-14 | 5 | The edge publishes `443/udp` (HTTP/3) but the security group admits TCP 80/443 only, so HTTP/3 never connects | Info | `compose.aws.yaml`; `aws ec2 describe-security-groups` | Accepted: harmless (clients fall back to HTTP/2); recorded so nobody "fixes" the group without a decision |
| SR-15 | 5 | FastAPI's `/docs`, `/redoc` and `/openapi.json` stay enabled in the app (default `docs_url`), but only `/api/*` reaches the API | Info | Live: `/docs` and `/openapi.json` → the SPA's `index.html`; `/api/docs`, `/api/openapi.json` → API `404` | Accepted: unreachable through the edge (area 5); local development uses them |

Fix size check (E3): every fix took well under an hour, with no migration, no
OpenAPI change and no new dependency.

## Verification log

Commands run from the reviewer's Mac on 2026-11-12 (UTC times where they
matter). Test node ids are relative to `apps/api`. No secret, cookie, token or
parameter value is recorded.

### Area 1: sessions

No gap; no test added.

- **Fixation.** Every login creates a new session and revokes the one on the
  request cookie: `tests/integration/test_auth_login.py::test_every_login_creates_a_new_session`,
  `::test_login_revokes_the_session_on_the_request_cookie`,
  `::test_login_as_another_user_revokes_the_previous_users_session`,
  `tests/integration/test_csrf.py::test_login_with_a_stale_cookie_and_no_token_rotates`.
- **Cookie.** `app/modules/identity/sessions.py` `set_session_cookie`:
  `__Host-` name when `SESSION_COOKIE_SECURE` (the default), `Secure`,
  `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`, `Max-Age` = the absolute
  timeout. `app/core/config.py` refuses `SESSION_COOKIE_SECURE=false` and an
  `http://` `APP_ORIGIN` unless `APP_ENV=local`. `render-env.sh` sets
  `SESSION_COOKIE_SECURE=true`. Clearing uses the same attributes:
  `tests/integration/test_auth_login.py::test_logout_clears_the_cookie_with_the_same_attributes`.
- **Token storage.** 32 random bytes; only the SHA-256 digest is stored
  (`hash_token`); the cookie value must be the canonical base64url of 32 bytes
  (`decode_token`).
- **Timeouts.** Staff 30 min idle / 12 h absolute; tutor, student, guardian
  7 days / 30 days; the strictest class wins: `tests/unit/test_session_limits.py`
  (`test_idle_limit`, `test_absolute_limit_even_with_recent_activity`,
  `test_an_expired_session_is_401_session_expired_before_the_csrf_check`).
- **Revocation.** Suspend/archive: `tests/integration/test_user_states.py::test_suspending_or_archiving_revokes_the_users_sessions`;
  role change: `tests/integration/test_session_revocation.py::test_adding_a_role_revokes_and_the_next_login_gets_the_new_class`,
  `::test_removing_a_role_revokes_and_the_next_login_gets_the_new_class`;
  password reset: `tests/integration/test_password_reset_redeem.py::test_issue_lookup_redeem_revokes_every_session`;
  password change rotates the current session and ends every other:
  `tests/integration/test_password_change.py::test_a_change_rotates_the_session_and_ends_every_other`.
- **Logout** revokes server-side; the old cookie is then `401`:
  `tests/integration/test_auth_session.py::test_login_me_logout_me`,
  `::test_logout_twice_is_401_the_second_time`.
- **Reauthentication** (ADR-0007 D10): `tests/integration/test_reauthenticate.py::test_reauthenticating_opens_a_five_minute_window`,
  `::test_a_later_reauthentication_restarts_the_window`, plus the per-action
  `reauth_required` tests (time-zone change, reversals, payroll confirms,
  personal exports).
- **Logs.** `tests/integration/test_auth_audit.py::test_no_password_token_or_hash_in_logs`,
  `::test_no_password_token_or_hash_in_any_audit_row`,
  `tests/integration/test_password_reset_redeem.py::test_no_password_or_token_reaches_a_log`,
  `tests/unit/test_passwords.py::test_password_never_appears_in_errors_logs_or_output`.
  One-time links carry their token in the URL fragment (`#token=`), which never
  reaches a server or a log.

### Area 2: CSRF

- `app/core/csrf.py`: `require_trusted_origin` is an app-level dependency
  (`app/main.py` `build_app`), so it runs on every route before
  authentication: exactly one `Origin`, equal to `APP_ORIGIN`, on `POST`,
  `PUT`, `PATCH`, `DELETE`. Signed-in routes also need `X-CSRF-Token` =
  HMAC-SHA256 of the session token (`app/core/authz.py`), compared in constant
  time. Tests: `tests/integration/test_origin.py`, `tests/integration/test_csrf.py`.
- **No CORS.** No `CORSMiddleware` anywhere in `app/`. Live: a preflight
  `OPTIONS` from `https://evil.example` → `405` with no `access-control-*`
  header; `GET /api/v1/auth/me` with that `Origin` → `401`, no
  `access-control-*` header.
- **State-changing `GET`.** `build_app(include_dev_routes=True)` has 166 `GET`
  routes. A script walked each handler and the app functions it calls (depth
  4) for `session.add/delete/merge/flush`, `insert/update/delete(`, job
  `defer`, audit writes and `last_seen`. Hits: `list.insert(0, …)` and
  `dict.update(…)` in four list helpers (false positives). The download routes
  (receipt and payslip PDFs, export files) write nothing. Verdicts:

  | `GET` route(s) | What it writes | Verdict |
  |---|---|---|
  | Every signed-in route (session resolver `identity.service.resolve_session_principal`) | `auth_sessions.last_seen_at`, at most once a minute (`sessions.touch_session`) | Accepted: session bookkeeping, ADR-0007 D8; no business state |
  | `GET /api/v1/notifications/stream` | Nothing (holds a subscription in memory) | No write |
  | All others | Nothing | No write |

  Marking notifications read is `POST` only (`/read-all`, `/{id}/read`).
- **Live** (`POST https://dev.classora.dev/api/v1/auth/login`, a dummy body):

  | `Origin` | Result |
  |---|---|
  | none | `403 csrf_failed` |
  | `https://evil.example` | `403 csrf_failed` |
  | `null` | `403 csrf_failed` |

### Area 3: headers and CSP

Live, `curl -sS -o /dev/null -D - https://dev.classora.dev<path>` (before
this PR's edge change is deployed):

| Response | Headers seen |
|---|---|
| `/`, `/some/deep/link` (SPA, `200`) | CSP exactly as `_docs/security-headers.md` (with the accepted `style-src 'unsafe-inline'`), `x-frame-options: DENY`, `x-content-type-options: nosniff`, `referrer-policy: strict-origin-when-cross-origin`, `permissions-policy` denying camera, microphone, geolocation, payment, usb, HSTS `max-age=31536000`, `cache-control: no-cache`, `server: Caddy` |
| `/assets/index-*.js` | the same SPA headers, `cache-control: public, max-age=31536000, immutable` |
| `/api/v1/auth/me` (`401`), `/api/v1/does-not-exist` (`404`) | `cache-control: no-store`, `nosniff`, `content-type: application/problem+json`, HSTS, `x-request-id`, `server: uvicorn`, `via: 1.1 Caddy` |
| `429` from the general limit (area 4b) | `Retry-After: 54` (the 4b script recorded only this header; the middleware sits inside the security-headers middleware, `tests/unit/test_api_rate_limit.py::test_the_429_is_the_rate_limited_problem_with_every_api_header`) |

Everything matches `_docs/security-headers.md` except the product headers
(SR-03, fixed). After this PR deploys, `server` and `via` are gone; the edge
smoke test checks it on every image build.

### Area 4: rate limits and client-IP spoofing

Code and tests:

- `app/core/client_ip.py`: `X-Forwarded-For` is read only when the TCP peer
  is in `TRUSTED_PROXIES`, from the right; IPv6 keys by `/64`; a non-IP peer
  is `unknown`. Tests: `tests/integration/test_rate_limits.py::test_a_spoofed_forwarded_for_is_ignored_without_trusted_proxies`,
  `::test_a_trusted_proxy_gives_each_forwarded_client_its_own_counter`,
  `tests/unit/test_client_ip.py`, `tests/unit/test_api_rate_limit.py::test_forwarded_for_from_an_untrusted_peer_is_ignored`.
- Uvicorn runs with `--no-proxy-headers` (`tests/unit/test_api_image.py::test_the_api_command_disables_uvicorn_proxy_headers`).
  `render-env.sh` sets `TRUSTED_PROXIES=172.30.0.10/32`, the edge's fixed
  address. The Caddyfile has no `trusted_proxies`, so Caddy replaces any
  client `X-Forwarded-For` with the TCP peer (`smoke.sh` "spoofed
  X-Forwarded-For is replaced by the peer").
- Limits (`app/core/ratelimit.py`): `login.ip` 50 requests / 15 min,
  `login.username` 10 failures / 15 min (existing or not), `reauth.user` 5,
  `token_redemption.ip` 20, `registration.ip` 10 / h, and the per-user
  domain limits; #163's general limit 600 / min / IP per API process
  (`app/core/api_rate_limit.py`); SSE `MAX_STREAMS_PER_USER = 10`
  (`tests/integration/test_live_notifications.py::test_the_eleventh_stream_gets_429_and_a_closed_one_frees_a_place`).
- Enumeration: one Argon2id verification against a dummy hash when the user
  or hash is missing (`identity/auth.py`), and the same body:
  `tests/integration/test_auth_login.py::test_no_account_enumeration`.
- Neither DEV nor the monitoring host has an IPv6 address (no `AAAA`
  record, `Ipv6Address: null`), so every client arrives over IPv4 with its
  own address; the IPv6 rules in the security groups are inert today.

Live:

- **b. General limit.** 2026-11-12 17:34:01 UTC, 610 `GET /api/v1/auth/me`,
  8 in parallel, each with a different `X-Forwarded-For`, `X-Real-IP`,
  `Forwarded`, `True-Client-IP` and `CF-Connecting-IP`, all inside one minute
  window (5.3 s): **600 × `401`, then 10 × `429`** with `Retry-After: 54`.
  The spoofed headers never split the budget. (The 600 includes nothing
  else: the window had just started.)
- **a. Login limit** (run last). 2026-11-12 17:35:05 UTC, 51 failed logins
  with distinct non-existent usernames (`sr150-nobody-01` …`-51`), the right
  `Origin`, and the same five spoofed headers rotated per request: **50 ×
  `401 invalid_credentials`, the 51st `429 rate_limited`** with
  `Retry-After: 589` (the window ends 17:45). A first attempt at 17:34:32 was
  void: it started inside 4b's blocked minute, so all 51 got the general
  limit's `429` before reaching the login route (no login counter moved); it
  was rerun once after the window. That rerun is why the probes total about
  750 requests instead of the planned 700.

### Area 5: edge bypass (reworded AC2)

Live against DEV (Elastic IP `3.136.144.244`) and the monitoring host
(`18.116.106.237`):

| Check | Result |
|---|---|
| `curl -D - http://<DEV EIP>/` | `308` to `https://<EIP>/` (Caddy's HTTP→HTTPS redirect, nothing else) |
| `https://<DEV EIP>/` with no SNI; `openssl s_client -noservername` | TLS alert 80 (internal error), no certificate: handshake fails |
| `https://evil.example/api/v1/auth/me` resolved to the EIP (foreign SNI) | TLS handshake fails |
| `https://dev.classora.dev/api/v1/auth/me` with `Host: evil.example` | `200`, 0 bytes, `server: Caddy`, no `x-request-id`: never reached the API (SR-08) |
| `http://dev.classora.dev/api/v1/auth/me` | `308` to HTTPS |
| `nc -z -G 5` to the DEV EIP on 22, 2019, 3000, 4317, 4318, 5432, 6432, 8000, 8080, 8443, 9090 | all closed or filtered |
| `/health/live`, `/health/ready` | `404`, empty |
| `/docs`, `/openapi.json` | the SPA's `index.html` (`/api/docs`, `/api/openapi.json` → API `404`) |
| Monitoring EIP: 22, 2019, 3000, 3100, 3200, 4317, 4318, 9090, 9093, 8888 | all closed or filtered; only 80 and 443 open |
| `https://grafana.classora.dev/` | `302 /login`; `/api/dashboards/home`, `/api/datasources` → `401` (no anonymous access) |

Read-only AWS (`aws ec2 describe-security-groups`, profile `jsolisdev`):

- `classora-dev-host`: TCP 80 and 443 from `0.0.0.0/0` and `::/0`, nothing else.
- `classora-monitoring-host`: TCP 80 and 443 from anywhere; TCP 4318 only
  from the `classora-dev-host` and `classora-prod-host` groups.

Code: `infrastructure/host/compose.aws.yaml` publishes host ports only on
`edge` (80, 443, 443/udp); `api` has `ports: !reset []`; `db`, `pgbouncer`,
`worker` and `collector` publish nothing (confirmed on the DEV host with
`docker ps`: only `classora-edge-1` has host bindings; `2019/tcp` is exposed
inside the network, not published). Caddy's admin endpoint stays on
`127.0.0.1:2019` inside the container (the healthcheck uses it from there).

**PROD equivalence (code only, no request).** `infrastructure/terraform/envs/prod`
uses the same `modules/app_host` (the same 80/443-only security group),
deploys the same release bundle (`infrastructure/host/compose.aws.yaml`,
`render-env.sh` with `TRUSTED_PROXIES`, `SESSION_COOKIE_SECURE=true`) and the
same `web` image, built from `infrastructure/docker/edge/Caddyfile`. The live
DEV results therefore stand for PROD's configuration; the #156 checklist
repeats the port and `Host` checks on PROD after go-live.

No path reaches the API, PostgreSQL, PgBouncer or Caddy's admin endpoint
except through Caddy's site on 443 (and 80's redirect). AC2 holds.

### Area 6: secrets

- `make scan-secrets` (gitleaks v8.30.1, full history, all refs, redacted):
  840 commits scanned, **no leaks found**.
- Image secrets: the CI `images` job's `check-images.sh` reports "no secret
  in Env" for all three images, and `make check-images` refuses a `.env` in
  the build context. A separate Trivy `--scanners secret` run of the ECR
  images was **not** run: it needs an ECR login and a pull of three arm64
  images onto the 8 GB machine, and the ECR pull path is the one the BRIEF
  warns about; gitleaks over the full history plus the `Env` check cover the
  same sources (the images are built from the repository and receive no
  secret at build time, ADR-0012). Recorded as a gap in method, not a finding.
- DEV host, one read-only SSM shell command (`AWS-RunShellScript`, printing
  names only): `/opt/classora/env/app.env` is mode `600 root:root`; the
  `api` and `worker` containers carry neither `POSTGRES_PASSWORD` nor
  `DATABASE_MIGRATION_URL` (the owner credential stays out, #165); their
  variable names match `compose.app.yaml` (the empty `STORAGE_*` names are
  the deferred file system's).
- Deploy document: `modules/app_host/deploy.tf` `allowedPattern =
  "^[0-9a-f]{40}$"` for `sha`.
- GitHub: `gh secret list` (repository) is empty; the `dev` and `production`
  environments hold no secret, only the variables `AWS_ROLE_ARN` and
  `OPS_BUCKET`; the repository variable `AWS_ROLE_RELEASE_CHECK_ARN`. Matches
  ADR-0012 (OIDC only).
- OIDC trust (`infrastructure/terraform/bootstrap/github_oidc.tf`):
  `aud = sts.amazonaws.com`; the deploy roles trust only
  `repo:<owner>@<id>/<name>@<id>:environment:<dev|production>` (immutable
  ids), the release-check role only `…:ref:refs/heads/main`. The environments'
  deployment branch policies allow only `develop` (`dev`) and `main`
  (`production`, with a required reviewer).
- Parameter Store (`aws ssm describe-parameters`, names only):
  `/classora/dev/owner_initial_password` still exists (SR-04).
- **PROD was not touched.** One AWS read, `aws ec2 describe-instances`
  without an instance filter, listed every running instance of the account,
  the PROD instance's id and public IP included. No request, SSM command or
  further read was sent to PROD; every later AWS call named the DEV or
  monitoring resources explicitly.

### Area 7: dependency posture

- `make security` (2026-11-12, exit 0):
  - `audit-api`: pip-audit 2.10.1 over 94 pinned packages of
    `apps/api/uv.lock`: **no known vulnerabilities**;
  - `audit-web`: `pnpm audit --audit-level high`: 2 moderate, 0 high, 0
    critical (SR-07);
  - `scan-secrets`: no leaks (area 6);
  - `sast`: Semgrep 1.179.0, 1,863 files, INFO/WARNING pass 0 findings,
    ERROR pass 0 findings.
- Images (Trivy 0.75.0, HIGH and CRITICAL): CI `images` job on `7327b4c9` and
  the `deploy-dev` pre-push scan on `4e962322`: api (debian 13.7) 109 HIGH,
  web (Caddy binary) 27 HIGH, pgbouncer (debian 13.7) 117 HIGH, **0
  CRITICAL**; most HIGH rows have no fixed version yet; 7 rows do (SR-06).
- Exception files: `apps/api/pip-audit-ignore.txt` and `.trivyignore` have
  no entries; `pnpm-workspace.yaml` has no `ignoreGhsas`; `.gitleaks.toml`
  has four narrow allowlists (one rule, one path, an exact match), each still
  needed for the files or the history it names.
- Dependabot: alerts **disabled** (SR-01); version updates for GitHub
  Actions only (`.github/dependabot.yml`, ADR-0016 E5). Code scanning: not
  used (Semgrep in CI instead).
- Runtimes (ADR-0001): Python 3.14, Node 24 (LTS), PostgreSQL 18.6,
  `python:3.14-slim` and `caddy:2.11.4` by digest: all supported upstream.

### Area 8: authorization

Evidence only, no re-sweep.

- `apps/api/tests/authz/idor.toml` (#149, checked by
  `tests/unit/test_idor_inventory.py` in CI): 119 routes reachable by a
  tutor, student or guardian with an id; 108 name their
  hidden-equals-missing tests, 11 are `exempt` (shared catalog ids:
  academic periods, classrooms, closures, education levels, evaluation
  templates, subjects; and catalog-id filters on lists scoped by caller:
  cohorts, groups, students, subjects, evaluation templates). **None is
  `pending`.** Each exemption reason was read and holds.
- `apps/api/tests/authz/jobs.toml` (#640, `tests/unit/test_job_inventory.py`):
  21 Procrastinate tasks: 5 `reader_checked`, 1 `actor_rechecked` (import
  commit), 15 `no_reader_output`.
- **Nested body ids by hand.** A script walked the OpenAPI request bodies of
  every in-scope route for `*_id`/`*_ids` below the top level and for UUID
  fields of any other name:

  | Route | Field | Scoped by | Test |
  |---|---|---|---|
  | `PUT /api/v1/class-sessions/{class_session_id}/attendance` | `marks[].student_id` | must be a participant of that session, else the same `422 student_not_participant` for unknown and foreign | `tests/integration/test_attendance.py::test_a_non_participant_is_the_same_422_for_unknown_and_foreign` |
  | `PUT /api/v1/learning-plans/{plan_id}` | `objectives[].id`, `objectives[].milestones[].id` | `workflows/learning_plans.py::_id_issues`: each id must be this plan's (and the milestone's own objective's), else `422 objective_unknown` / `milestone_unknown` | `tests/integration/test_learning_plans.py::test_the_put_id_semantics` (unknown, foreign, repeated and moved ids) |
  | `POST /api/v1/notifications/read-all` | `up_to` | must be one of the caller's notifications, else `404`; the update is filtered by recipient | `tests/integration/test_notifications.py::test_a_user_never_sees_counts_reads_or_bulk_reads_another_users_notifications` |

- #640's side notes: notifications are not re-filtered at read time, and
  skip notices are written for suspended users (SR-11, accepted); recipients
  did not exclude deactivated guardian profiles (SR-05, fixed). "Delegation
  revocation lacks its own test" is covered at request time by
  `tests/integration/test_class_session_overrides.py::test_a_delegated_administrator_overrides_until_revoked`
  and `tests/integration/test_payroll_workflow_access.py::test_n6_a_replay_after_the_delegation_is_revoked_is_403`;
  jobs re-check the actor's permissions, not the delegation
  row, so no job-side gap.

### Area 9: availability notes

Recorded only; #152 measures.

- Receipt and payslip PDFs render in the API process
  (`app/core/pdf.py`, `anyio.to_thread.run_sync`), not the worker (SR-12).
- Bounds today: authentication and record policy on every PDF route; #163's
  general limit, 600 requests per minute per client IP per API process; the
  SSE cap of 10 streams per user per process; and, from this PR, a 4 MB
  request-body cap at the edge (SR-02). The API container has a 512 MiB
  memory limit (`compose.aws.yaml`).

## Accepted risks

- SR-07 (dev-tool moderate advisories), SR-08 (empty `200` for an unmatched
  `Host`), SR-09 (no `includeSubDomains`/`preload`), SR-10 (full headers in
  the host's edge log), SR-11 (no read-time notification re-filter), SR-12
  (PDFs in the API process), SR-13 (Grafana's anonymous health endpoint),
  SR-14 (HTTP/3 published, never admitted), SR-15 (FastAPI docs enabled but
  unreachable): reasons in the findings table.
- Already owner-accepted, not re-opened: `style-src 'unsafe-inline'` (#24
  E2); the per-IP login limit shared behind one NAT (ADR-0008 D15).
- No high finding, so no accepted-risk ADR.

## Owner actions

- **O1.** Turn on Dependabot alerts (Settings → Code security) (SR-01;
  ADR-0016 "Owner-only GitHub settings").
- **O2.** Delete `/classora/dev/owner_initial_password` from Parameter Store
  now that the DEV Owner has signed in (SR-04).
- **O3 (#156 go-live checklist).** Confirm `/classora/prod/owner_initial_password`
  is deleted after the PROD bootstrap; re-run area 5's port, SNI and `Host`
  checks against PROD; confirm the `server`/`via` headers are gone on PROD
  after the release that carries this PR.

## Follow-ups

- **SR-06 (low):** refresh the image bases so the fixed HIGH advisories land
  (`zlib1g` in the api and pgbouncer images, `apt` in pgbouncer, `go-jose/v4`
  via a Caddy patch release), and decide whether fixable HIGH rows should
  get a review date: follow-up #648.
