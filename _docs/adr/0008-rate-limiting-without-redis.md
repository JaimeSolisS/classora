# ADR-0008: Rate limiting without Redis

## Status

Accepted

## Date

2026-10-18

## Sign-off

Owner: JaimeSolisS. Decision items D15 and D16 approved as recommended (option (a) for each) on 2026-10-18 (GitHub issue #19, M2-01, owner decision comment). No amendments. The D16 follow-up is GitHub issue #163.

## Context

Resolves architecture §10 **ISSUE-S3** (rate limiting without Redis) in [`../architecture.md`](../architecture.md) §10.7. Records decision items **D15** and **D16** of issue #19.

- Spec [`../spec.md`](../spec.md) §10 (application stack) says rate limits use "Caddy/FastAPI enforcement, without Redis", and excludes Redis and Celery. It warns that Redis-free rate limiting across more than one API process needs a shared store or an enforced edge or proxy policy. Spec §14 repeats that sessions and jobs live in PostgreSQL. Spec §18 item 4 leaves the details of rate-limit enforcement open and asks for an explicit implementation decision. This ADR is that decision for M2, so the spec is not changed.
- Standard Caddy has no rate-limit directive. It needs a third-party module and a custom build.
- FastAPI in-memory limits are per process, so they are not correct across several API worker processes.
- Behind a reverse proxy (Caddy in AWS; see the 2026-11-11 amendment), the TCP peer is not the client. The client IP must come from a forwarded header, trusted only from a known proxy.
- Authentication endpoints are the real brute-force target. The center has one location and a small user base, and parents may share the center's Wi-Fi (one IP).

## Decision

### Authentication endpoints (D15 (a))

Fixed-window counters stored in PostgreSQL, keyed by IP and by account, shared by all API processes. Windows are 15 minutes unless stated:

- **Login:** 10 *failed* attempts per normalized submitted username, whether or not the account exists, and 50 attempts per IP.
- **Reauthentication:** 5 failures per user.
- **Token redemption:** 20 attempts per IP.
- **Registration requests:** 10 per IP per hour.

Behaviour:

- Going over a limit returns `429` with `Retry-After` and the same body whether or not the account exists.
- Nothing is locked permanently. A window simply expires.
- A periodic job deletes old windows.

### Client IP

- The client IP is the TCP peer, unless the peer is in the configurable `TRUSTED_PROXIES` list, which is empty by default.
- Only when the peer is trusted is the right-most untrusted `X-Forwarded-For` entry used. An `X-Forwarded-For` header from an untrusted peer is ignored.
- In AWS the trusted peer is the Caddy container, the first hop ([ADR-0010](0010-hosting-edge-and-https.md)). Caddy ignores any client-sent `X-Forwarded-For` and sets it to the TCP peer it sees.

### General API (D16 (a))

- M2 has **no general API rate limit**.
- Follow-up issue #163 (M10-09) adds a coarse in-process limit per API worker process and client IP (for example 600 requests per minute) during the Phase 1 local hardening step. It reuses the client-IP resolution above. Done; see the 2026-11-12 amendment.
- That limit is per process on purpose. Spec §10 notes that in-process limits are not shared across processes. For a coarse safety limit on one small host this is acceptable, while the authentication limits, which must be correct across processes, use the shared PostgreSQL counters.
- A Caddy rate-limit module is **not** required.

## Consequences

- #27 (M2-09) implements the authentication limits, the client-IP resolution, `TRUSTED_PROXIES` and the cleanup job. #29 (M2-11) depends on it for token redemption and registration.
- Each limited request costs a small PostgreSQL write. This stays on the existing database, with no Redis.
- An attacker can slow a known user's logins for at most 15 minutes at a time, but cannot lock the account permanently.
- Parents behind the center's shared Wi-Fi share the per-IP budget; 50 login attempts per 15 minutes leaves room for them.
- Every other endpoint has the general in-process limit of D16, implemented in #163 (see the 2026-11-12 amendment).
- M9-02 (#610) configures the edge Caddy to set the forwarded header from the TCP peer, and M9-06 (#614) adds Caddy's fixed address to `TRUSTED_PROXIES` in AWS.
- Revisit if the API is scaled beyond one host (spec §10, §14).

## Alternatives considered

- **5 failed logins per username per 15 minutes** (D15 (b)). Rejected: an attacker could keep a known user locked out by sending wrong passwords.
- **Counting per IP only** (D15 (c)). Rejected: a slow distributed guessing attack on one account would go unthrottled.
- **Building the general in-process limit now as an extra M2 issue** (D16 (b)). Rejected: more M2 scope for a single center with a small user base, where authentication is the real brute-force target.
- **Requiring a Caddy rate-limit module** (custom build; D16 (c); ADR-0010, decided in M9-01 (#609), confirms there is none). Rejected: it pushes the decision to Phase 2/3, and the module does not exist locally.

## Amendments

### 2026-10-20: per-user domain limits for session requests (#59, owner-delegated)

Recorded with M4-14 (#59 E2 (a), the recommended option, accepted under the owner's delegation for the M4 run). D15 and D16 are otherwise unchanged.

- A `requests` policy may be keyed by the **authenticated user** (key `user`) instead of the client IP. The route's dependency resolves the signed-in principal first (`401` before `429`) and ends the transaction the session lookup opened before the counter statement (one connection per request at a time). Keys are stored as SHA-256 hashes, as for the other policies.
- Two domain limits use it, on the same `rate_limit_counters` table (no new dependency, no Redis):
  - `available_slots.user`: 60 per 15 minutes on `GET /api/v1/available-slots` (a read limit);
  - `session_request.user`: 10 per hour on `POST /api/v1/session-requests` (a write limit).
- They are keyed by user because families share the center's Wi-Fi: a per-IP budget would let one family throttle another. Every request counts, `4xx` answers included.
- They are separate from the general in-process API limit of D16 (#163), which is unchanged.

### 2026-10-23: a per-user limit for change requests (#62, owner-delegated)

Recorded with M4-17 (#62 S4, the groomed choice under the owner's delegation for the M4 run). The 2026-10-20 amendment above is otherwise unchanged.

- `change_request.user`: 10 per hour on `POST /api/v1/change-requests` (a write limit; cancellation and reschedule requests by guardians, students and tutors), keyed by the signed-in user, every request counted (`4xx` included), on the same `rate_limit_counters` table.

### 2026-11-11: Caddy is the first hop (ADR-0010)

Recorded with M9-01 (#609), after the owner's M9 re-plan of 2026-11-11 removed CloudFront ([ADR-0010](0010-hosting-edge-and-https.md)). D15 and D16 are otherwise unchanged.

- There is no CloudFront in front of Caddy. Caddy on each host is the **first hop**: it discards any `X-Forwarded-For` the client sends and sets it to the TCP peer.
- The app network has a fixed subnet and Caddy a fixed address, which is the only `TRUSTED_PROXIES` entry in AWS. The client-IP rule above is unchanged: a trusted peer's right-most untrusted `X-Forwarded-For` entry is the client.
- The stock Caddy build is used, with no rate-limit module.

### 2026-11-12: the general in-process limit (#163, owner-delegated)

Recorded with M10-09 (#163 E1–E6, each the recommended option (a), accepted under the owner's delegation for the M10 run; orchestrator-recommended, pending owner review). It implements D16 (a). D15 and its amendments are unchanged.

- **Value (E1).** A setting, `API_RATE_LIMIT_PER_MINUTE`: requests per minute per API process and client IP, default **600**, range 0–100000, `0` turns it off. DEV and PROD use the default (no infrastructure change). The E2E stack sets 60000, because the parallel Playwright run reaches the API from one IP.
- **Algorithm (E2).** Fixed 60-second windows aligned to the epoch minute (UTC, from the application clock), as in D15. Only the current window's counters are kept in memory and the map is replaced when the window changes. A burst of up to twice the limit across a boundary is accepted. At most 10,000 clients are tracked per window: a new client beyond that is let through uncounted (fail open), with one warning per window.
- **What counts (E3).** Every HTTP request, counted before routing, authentication, the `Origin` check and the database, except `GET /health/live` and `GET /health/ready` (health checks, the deploy readiness gate and monitoring). A refused request never reaches the D15 or domain counters. An SSE connect counts once.
- **Key (E4).** The client IP from the rule above (`TRUSTED_PROXIES`, IPv6 by `/64`; no resolvable peer shares one `unknown` key).
- **Answer and logs.** `429 rate_limited` with the same detail as the authentication limits and `Retry-After` (seconds to the window's end, at least 1). The answer keeps `X-Request-ID`, the API security headers and the access line. One `WARNING` `api_rate_limit.exceeded` on `app.security` per client per window, with the method and the policy code `api.ip`, never the IP or the raw path.
- **Wiring (E5).** `app.core.api_rate_limit`, a pure ASGI middleware inside the request-context and security-header middleware. Only `create_app()` installs it; `build_app()` installs it only when given a limit, so tests and the OpenAPI export are unchanged.
- **Per process.** Each AWS host runs one uvicorn process (`infrastructure/docker/api.Dockerfile`, no `--workers`), so the limit is per host there. Staff and families behind the center's one public IP share 600 requests per minute. A busy center that hits it needs a new value, which on AWS is a `render-env.sh` change and a deploy.

## References

- [`../architecture.md`](../architecture.md) §4.3 step 3, §10.7 ISSUE-S3, §10.10 ISSUE-I3, §11
- [`../spec.md`](../spec.md) §10 (application stack: rate limits; Redis and Celery excluded), §14 (no Redis/Celery), §18 item 4
- [ADR-0007](0007-authentication-sessions-recovery-and-delegation.md) (authentication, sessions and reauthentication)
- GitHub issue #19 (this decision gate, items D15 and D16); #27 (M2-09, authentication rate limits); #163 (M10-09, general in-process API rate limit)
