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
| SR-02 | 3, 9 | No request-body size limit: FastAPI reads the whole body before any dependency (the `Origin` check, authentication), and neither Caddy, uvicorn nor the app capped it, so one unauthenticated `POST` of hundreds of MB could exhaust the API container's 512 MiB | Medium | FastAPI 0.143.3 `fastapi/routing.py`: `await request.body()` (line 458) before `solve_dependencies` (line 511); no `request_body` in the Caddyfile; no `--limit-*`/body cap in the uvicorn command. Not reproduced on DEV (destructive). | Fixed in this PR (test: `infrastructure/docker/edge/smoke.sh` "POST /api 5 MB body status" and "… gets no upstream answer"; Caddy `request_body max_size 4MB`) |
| SR-03 | 3 | Product disclosure: `server: uvicorn` and `via: 1.1 Caddy` on API answers, `server: Caddy` on the SPA | Low | Live: `GET /api/v1/auth/me` → `server: uvicorn`, `via: 1.1 Caddy`; `GET /` → `server: Caddy` | Fixed in this PR (tests: `tests/unit/test_api_image.py::test_the_api_command_sends_no_server_header`, `smoke.sh` "GET / has no Server header", "/api has no Server header …", "/api has no Via header") |
| SR-04 | 6 | The DEV bootstrap parameter `/classora/dev/owner_initial_password` still exists after the first sign-in | Low | `aws ssm describe-parameters` (names only): present, last modified 2026-11-12 | Owner action O2 (and O3 for PROD, #156) |
| SR-05 | 8 | Family notifications (session moves, series edits, closure skips, substitutions) still went to a linked guardian whose profile is **deactivated**, who has lost access to the student; params are ids and instants only | Low | `families.service.student_notification_recipients` had no `Guardian.is_active` filter, unlike `families.facts` (#640 QA side note) | Fixed in this PR (test: `tests/integration/test_class_series_closure_changes.py::test_a_guardian_with_a_deactivated_profile_is_not_told`) |
| SR-06 | 7 | Images carry HIGH OS/Go advisories that have fixed versions: `zlib1g` (api, pgbouncer), `apt`/`libapt-pkg7.0` (pgbouncer), `go-jose/v4` in Caddy 2.11.4 (web). No CRITICAL. ADR-0016 fails only on fixed CRITICAL | Low | CI `images` job (run 43245977655) and `deploy-dev` pre-push scan (run 43266931107): api 109 HIGH, web 27 HIGH, pgbouncer 117 HIGH, 0 CRITICAL; 7 rows `fixed` | Follow-up (orchestrator files it; see "Follow-ups") |
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
