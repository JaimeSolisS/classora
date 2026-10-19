# Classora API conventions

This document lists the conventions every API endpoint follows. It does not
list endpoints: the committed OpenAPI document
([`packages/api-client/openapi.json`](../packages/api-client/openapi.json)) is
the endpoint reference (architecture ISSUE-X4).

Each rule names its source and its status:

- **Approved**: decided in the spec, an approved architecture section or an
  accepted ADR.
- **PROPOSED (decided by …, implemented by …)**: written down in the
  architecture but not yet approved or built. Treat it as the plan, not as a
  final rule.

## Paths

- Product endpoints live under `/api/v1/<domain>/...`.
  Approved (spec §10 "API versioning", architecture §4.4).
- The health probes stay at the root: `GET /health/live` and
  `GET /health/ready`. They are unauthenticated and never expose internal
  details. Approved (architecture §9.3).
- The SPA and the API share one origin, so there is no CORS and no API URL
  setting in the web app. In local development the Vite dev server forwards
  `/api` and `/health` to the API. Approved (architecture §4.4).
- Resource names are plural and kebab-case, for example
  `/api/v1/class-sessions`. PROPOSED (decided by architecture ISSUE-G2,
  implemented by the first scheduling endpoint task).

## Schemas

- Pydantic schemas are operation-specific: `XCreate`, `XUpdate`, `XDetail`,
  `XListItem`. Approved (architecture §4.4).
- IDs are UUIDs. Approved (spec §10 "Keys/time").
- Instants are ISO 8601 strings with an offset, stored as UTC `TIMESTAMPTZ`.
  Approved (spec §10, ADR-0004).
- Calendar dates are `YYYY-MM-DD`. Recurring class times are a local time plus
  a duration and a `TZID`. Approved (ADR-0004).

## Errors

Every error response is an RFC 9457 Problem Details body with
`Content-Type: application/problem+json`. Approved (spec §10 "API errors",
architecture §4.3 step 7); implemented by M1-09 in `apps/api/app/core/errors.py`.

| Field        | Always | Meaning |
| ------------ | ------ | ------- |
| `type`       | yes    | Always `"about:blank"`. |
| `title`      | yes    | The HTTP reason phrase, for example `"Not Found"`. |
| `status`     | yes    | The HTTP status code. |
| `code`       | yes    | A stable `snake_case` code. The client maps it to a translated message. |
| `request_id` | yes    | The same value as the `X-Request-ID` response header. |
| `detail`     | no     | A fixed, safe sentence. Never built from submitted values. |
| `errors`     | no     | Field errors (422): a list of `{path, code, message}`. |

- A field error's `path` is the location as a list of strings and integers
  (for example `["body", "items", 0, "amount"]`), `code` is the Pydantic error
  type (for example `missing`) and `message` is its text. The submitted value is
  never included.
- No response contains internal details (exception class or message, traceback,
  SQL, DSN, host names) in any environment. Unexpected exceptions become
  `500 internal_error` and are logged with their traceback.
- The OpenAPI document contains one `ProblemDetails` schema (and `FieldError`
  for the `errors` items). Every operation that can return `422` documents it as
  `ProblemDetails` with `application/problem+json`; FastAPI's default
  `HTTPValidationError` schema is removed. An endpoint documents its other error
  statuses with `responses={503: problem_response_doc("...")}`.
- A module defines a new error as an `AppError` subclass with class attributes
  only, and raises it. Routes never build Problem JSON by hand:

  ```python
  class ThingNotFoundError(AppError):
      status = 404
      code = "thing_not_found"
      detail = "The thing does not exist."
  ```

Codes in use today:

| Code | Status | Source |
| ---- | ------ | ------ |
| `validation_error` | 422 | Request validation (body, query, path, headers). |
| `not_found` | 404 | Unknown path, or a record that is missing or not visible to the caller (no `detail`). |
| `authentication_required` | 401 | No signed-in user on an `authenticated()` or `require()` route. |
| `session_expired` | 401 | The session cookie belongs to a session that timed out (idle or absolute limit); sign in again (see Sessions). |
| `invalid_credentials` | 401 | `POST /api/v1/auth/login` did not succeed, for any reason (the same body whether or not the account exists). |
| `permission_denied` | 403 | The user lacks the route's permission, or may see the record but not take the action. |
| `csrf_failed` | 403 | A `POST`, `PUT`, `PATCH` or `DELETE` whose `Origin` is not the app origin, or (on a signed-in route) whose `X-CSRF-Token` is missing or wrong. One code for both checks (see CSRF and `Origin`). |
| `reauth_required` | 403 | A sensitive action whose session has no password reauthentication in the last 5 minutes; reauthenticate and retry (see Reauthentication). |
| `reauth_failed` | 403 | `POST /api/v1/auth/reauthenticate` with a wrong password (or a user without one). The session stays signed in. |
| `current_password_invalid` | 403 | `POST /api/v1/auth/password` with a wrong current password (or a user without one). The session stays signed in (see Password reset and change). |
| `rate_limited` | 429 | Too many attempts on a rate-limited endpoint (login, reauthentication and the password change, one-time link lookup and redemption, self-registration); a `Retry-After` header gives the seconds to wait. The same body whether or not the account exists (see Rate limits). |
| `token_invalid` | 410 | A one-time link (invitation or password reset) that is unknown, malformed, used, replaced or expired, of the other kind, or that can no longer be redeemed. One body for every case (see Invitations). |
| `username_taken` | 409 | `POST /api/v1/invitations` or `POST /api/v1/auth/registrations` with a username another user already has, in any state. |
| `registration_already_decided` | 409 | Approving or rejecting a registration request that was already approved or rejected, or whose account is no longer `pending` (see Registration requests). |
| `user_not_active` | 409 | `POST /api/v1/password-resets` for a user who is not `active` (pending, suspended or archived). |
| `last_active_owner` | 409 | Suspending or archiving the only active Owner, or taking the `owner` role from them (see User administration). |
| `user_state_transition_invalid` | 409 | A suspend, archive or reactivate the user's current state does not allow, including the same state (see User administration). |
| `username_invalid` | 422 | A username that does not normalize to 3 to 64 characters from `a-z 0-9 . _ -`. |
| `password_too_short`, `password_too_long`, `password_equals_username` | 422 | A new password that breaks the policy (12 to 128 characters, not the username; ADR-0007 D6). |
| `method_not_allowed` | 405 | Known path, wrong method. |
| other status-derived codes | 4xx/5xx | Any other framework HTTP error: the `snake_case` reason phrase (for example `unsupported_media_type`). |
| `internal_error` | 500 | Any unexpected exception. |
| `database_unavailable` | 503 | `GET /health/ready` when PostgreSQL does not answer. |
| `invalid_cursor` | 400 | A cursor that is malformed or too long (cursor pagination). |
| `version_conflict` | 409 | Optimistic concurrency conflict (see Versions). |

The local/DEV demo job route also uses `demo_job_already_queued` (409); it is
not part of the contract.

## Authentication and authorization

Approved (spec §2, §16, ADR-0007 D4, architecture §4.3 steps 4-5);
implemented by M2-03 in `apps/api/app/core/authz.py`.

- **Every route declares who may call it**, with exactly one of:
  - `public(reason="...")`: anyone; the reason says why.
  - `authenticated()`: any signed-in user; yields the `Principal`.
  - `require("<permission>")`: holders of one permission code from the
    catalog (`app/modules/identity/permissions.py`); yields the `Principal`.

  Each works in `dependencies=[...]` on a route or router, or as an annotated
  parameter (`principal: Annotated[Principal, require("user.view")]`). Declare
  it before any other dependency. Protected routes pass
  `responses=AUTH_RESPONSES` (`authenticated()`) or
  `responses=PERMISSION_RESPONSES` (`require()`), so the OpenAPI document lists
  their `401`/`403`; an unsafe `authenticated()` or `public()` route also adds
  `CSRF_RESPONSES` (its `403 csrf_failed`). The permission matrix (`_docs/testing-guidelines.md`)
  fails CI for a route without a declaration.
- **The current user** comes only from `get_current_principal`. A `Principal`
  holds the user id, role codes and effective permissions, never a secret.
  Services, workflows and jobs check a permission outside a route with
  `ensure_permission(principal, permission)`.
- **Check order.** Without a signed-in user the answer is `401
  authentication_required`; on an unsafe method a failed CSRF check is then
  `403 csrf_failed`; without the permission it is `403 permission_denied`; on
  a sensitive action without a recent reauthentication it is then `403
  reauth_required` (see Reauthentication). All
  come before path, query and body validation, so an unauthorized caller never
  sees a `422`. (A foreign `Origin` is refused even before the session, see
  below.) Exception: a body that is not
  parseable JSON gets `422` before any check (FastAPI parses it first); that
  response reveals nothing.
- **Records: 404, not 403, for what the caller may not see.** Services apply
  record policies with `authorize_record(record, can_view=..., can_act=...)`:
  - missing, or `can_view` false: `404 not_found` (`RecordNotFoundError`), the
    same body as an unknown path. A domain never raises its own 404 for a
    record that is missing or hidden;
  - visible but `can_act` false: `403 permission_denied` (the caller may see
    the record, so this reveals nothing new).
- **Policies** are pure, synchronous functions in the module's
  `permissions.py`, named `can_<action>_<resource>(principal, record, ...)`.
  The service loads the facts they need (assignments, guardian links) and
  passes them in. A user with several roles is allowed when any of the roles
  allows it.
- **Lists filter.** A list endpoint never answers `403` or `404` for items the
  caller may not see; its query returns only what the same policy allows.
- `401`/`403`/`404` bodies never name the permission, role, record or user.

### Sessions

Implemented by M2-05 and M2-07 (ADR-0007 D7, D8) in
`apps/api/app/modules/identity/`.

- **Endpoints:**
  - `POST /api/v1/auth/login` (public): body `{"username", "password"}`.
    `200` sets the session cookie and returns the signed-in user
    (`id`, `username`, `display_name`, sorted `roles` and `permissions`). Any
    failure is `401 invalid_credentials` with one fixed body, whether or not
    the account exists.
  - `POST /api/v1/auth/logout` (`authenticated()`): revokes the current session
    only, clears the cookie, `204`.
  - `GET /api/v1/auth/me` (`authenticated()`): the same body as login.
- **Cookie:** `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`, and
  `Max-Age` equal to the session's absolute timeout (`43200` for staff,
  `2592000` for the others; no `Expires`). The server stays the authority:
  the cookie lifetime never extends a session. With `SESSION_COOKIE_SECURE=true`
  (the default, every deployed environment) it is `__Host-classora_session`
  with `Secure`; with `false` (allowed only when `APP_ENV=local`, for plain-HTTP
  local runs) it is `classora_session` without `Secure`. The API reads only
  the current mode's name.
- **Token:** 32 random bytes in unpadded base64url. The server stores only its
  SHA-256 hash, so the token is never stored, logged, audited or returned in a
  body.
- **Rotation:** every login issues a new session; an unrevoked session on the
  request's cookie (expired or not) is revoked, and a token supplied by the
  client is never adopted.
- **Timeouts (M2-07):** fixed at login from the user's roles; the strictest
  class wins, and a user without roles counts as staff.

  | Class | Roles | Idle | Absolute |
  | ----- | ----- | ---- | -------- |
  | staff | Owner, Administrator, Academic Coordinator, Finance Staff | 30 minutes | 12 hours |
  | extended | only Tutor, Student and/or Guardian | 7 days | 30 days |

  A session expires when the absolute limit is reached or when it has been
  idle for the idle limit. Activity (`last_seen_at`) is recorded at most once
  a minute, only on requests that succeed.
- **`session_expired` vs `authentication_required`:** both are `401`. A
  cookie of a session that timed out gets `session_expired` (show "your
  session expired" and sign in again). No cookie, an unknown or revoked
  session, or a user who is not active gets `authentication_required`. On
  unsafe methods both come before the CSRF token check (`csrf_failed`); a
  foreign or missing `Origin` is still refused with `csrf_failed` before the
  session is looked at (see CSRF and `Origin`).
- **Revocation:** suspending or archiving a user, adding or removing a role,
  and setting a new password revoke all of that user's sessions in the same
  transaction (they then get `authentication_required`; reactivating the user
  does not bring them back). Delegation changes do not revoke sessions:
  permissions are loaded on every request.
- **Purge:** an hourly worker job deletes session rows 24 hours after they
  were revoked or expired.
- **CSRF token:** login and `/auth/me` also return `csrf_token`, the session's
  CSRF token (see below).

### CSRF and `Origin`

Implemented by M2-06 (ADR-0007 D9) in `apps/api/app/core/csrf.py`. The
browser-side headers (Content Security Policy) are in
[`security-headers.md`](security-headers.md).

- **Unsafe methods** are `POST`, `PUT`, `PATCH` and `DELETE`. `GET`, `HEAD` and
  `OPTIONS` are never checked, so they must never change state.
- **`Origin` (every route, `public()` ones included).** The request must carry
  exactly one `Origin` header equal to `APP_ORIGIN`, the origin the SPA is
  served from (`http://127.0.0.1:5173` locally; the `https://` hostname when
  deployed). A missing, `null`, duplicated or different value (another scheme,
  host or port; `localhost` is not `127.0.0.1`) is `403 csrf_failed`. The check
  runs before authentication, so a foreign origin gets `403` even without a
  session. Unknown paths and wrong methods still answer `404` and `405`.
  `public()` routes (login, and later token redemption and registration) rely
  on this check plus `SameSite`.
- **Token (every `authenticated()`/`require()` route).** An unsafe request
  also needs the header `X-CSRF-Token`, exactly once, equal to the session's
  token. The token is derived from the session token (base64url of
  HMAC-SHA256, 43 characters), so nothing extra is stored: it is the same for
  the whole session and in every tab, and changes on every login. `POST
  /api/v1/auth/login` returns the new session's token and `GET
  /api/v1/auth/me` the current one (`csrf_token`). It is never in a cookie.
- **Order:** `403 csrf_failed` for the `Origin` → `401` without a session →
  `403 csrf_failed` for the token → `403 permission_denied` → `422`.
- **The SPA** keeps the token in memory only (never `localStorage`,
  `sessionStorage` or a cookie), sends it on every unsafe request, and calls
  `/auth/me` again after a reload. On `csrf_failed` it refetches `/auth/me` and
  retries once (#28).
- **Errors and logs:** both checks answer the same `403 csrf_failed` body. The
  API logs one `WARNING` on `app.security` with the reason (`origin_missing`,
  `origin_mismatch`, `token_missing`, `token_invalid`), the method and the
  path, never the `Origin` value, the token or the cookie. Failures are not
  audited.
- **Swagger UI** (`/docs` on the API port) cannot send unsafe requests: its
  origin is not the app origin.

### Reauthentication

Implemented by M2-08 (ADR-0007 D10) in `apps/api/app/core/reauth.py` and the
identity module.

- **Endpoint:** `POST /api/v1/auth/reauthenticate` (`authenticated()`, needs
  `Origin` and `X-CSRF-Token`), body `{"password"}` (1 to 1024 characters).
  `200` returns `{"reauthenticated_until": "<ISO 8601 UTC>"}`; a wrong password
  (or a user without one) is `403 reauth_failed`, the session stays signed in
  and its window is unchanged. Each attempt runs one Argon2id verification and
  is audited (`identity.reauthenticated`, or `identity.reauth_failed`
  committed even though the response is an error). After 5 wrong passwords
  for the user in a 15-minute window, further attempts get `429 rate_limited`
  without a password check (see Rate limits).
- **Window:** 5 minutes from the reauthentication, for the **current session
  only**; activity does not extend it, a later reauthentication restarts it,
  and logging in does not count (the first sensitive action after a login asks
  for the password). Logout, revocation and expiry end it with the session.
  At exactly `+5m` it is over.
- **Declaring a sensitive route:** add
  `require_recent_auth("<action>")` next to the route's access declaration
  (`dependencies=[...]` or an annotated parameter; it yields the `Principal`)
  and pass `responses=REAUTH_RESPONSES`. When only some bodies are sensitive
  (a staff-role invitation, a settings change to payment methods or the time
  zone, a state change to suspended or archived), declare
  `guard: Annotated[RecentAuthGuard, recent_auth_guard("<action>")]` and call
  `guard.ensure()` when the condition holds. The answer is `403
  reauth_required`; the body never names the action.
- **Order:** `401` (`authentication_required` / `session_expired`) → `403
  csrf_failed` → `403 permission_denied` → `403 reauth_required` → `422`,
  wherever the declarations sit: a user without the permission is never asked
  for a password.
- **Actions:** `SENSITIVE_ACTIONS` in `app/core/reauth.py` lists every ADR-0007
  D10 action with the issue that applies it. A later issue applies an action
  with the marker, a `reauth` (and, for the guard, `reauth_conditional = true`)
  field in `apps/api/tests/authz/matrix.toml`, and adds to the action's
  **bound permissions** any new permission that only that action uses: an
  unsafe route requiring a bound permission (for example
  `user.password_reset.issue`) without the marker fails the matrix test.
  Markers are only for `POST`, `PUT`, `PATCH` and `DELETE`.

### Rate limits

Implemented by M2-09 (ADR-0008 D15) in `apps/api/app/core/ratelimit.py`
(policies, counters, the route declaration) and `apps/api/app/core/client_ip.py`
(the client address). The counters live in PostgreSQL (`rate_limit_counters`),
so a limit holds across every API process; there is no Redis and no permanent
lockout: a window simply ends.

| Policy | Endpoint | Limit | Key | Counts |
| ------ | -------- | ----- | --- | ------ |
| `login.ip` | `POST /api/v1/auth/login` | 50 per 15 min | client IP | every request |
| `login.username` | `POST /api/v1/auth/login` | 10 per 15 min | normalized submitted username, existing or not | failed attempts |
| `reauth.user` | `POST /api/v1/auth/reauthenticate` and `POST /api/v1/auth/password` (the current-password check); one shared budget | 5 per 15 min | user | failed attempts |
| `token_redemption.ip` | `POST /api/v1/auth/invitation/lookup`, `POST /api/v1/auth/invitation/redeem`, `POST /api/v1/auth/password-reset/lookup` and `POST /api/v1/auth/password-reset/redeem`; one shared budget | 20 per 15 min | client IP | every request |
| `registration.ip` | `POST /api/v1/auth/registrations` | 10 per hour | client IP | every request |

- **Fixed windows**, aligned to the Unix epoch in UTC (`:00`, `:15`, `:30`,
  `:45`; the full hour for registration). Up to twice a limit can pass across
  a window boundary; that is the accepted cost of fixed windows.
- **`429 rate_limited`** with `Retry-After`: the whole seconds until the
  exhausted window ends (rounded up, at least 1; at most 900 for login and
  reauthentication). The body never names the policy, the key or the account.
  Each `429` writes one `WARNING` line on the `app.security` logger with the
  policy code, method and path (no IP, username or user id) and no audit
  event.
- **Requests versus failures.** A `requests` policy takes one unit per request
  before anything else, so `401`, `403` and `422` answers count too. A
  `failures` policy reserves a unit before the password is verified (no
  verification at all when the limit is used up) and gives it back when the
  password is right; a success does not reset the window. Every count is
  committed in its own transaction, so an error response never rolls it back.
- **Order on login:** a foreign `Origin` (`403 csrf_failed`, never counted) →
  `login.ip` (`429`) → body validation (`422`; a body that is not JSON may be
  rejected before) → `login.username` (`429`) → the password check (`200` or
  `401 invalid_credentials`). The `429` does not depend on whether the account
  exists. On reauthentication: `401` / `csrf_failed` → `422` → `reauth.user`
  (`429`; the session and its window are unchanged) → the password check.
- **Client IP.** The client is the TCP peer. Only when the peer is listed in
  `TRUSTED_PROXIES` (comma-separated IPs or CIDR networks, empty by default)
  is `X-Forwarded-For` read, from the right: trusted entries are skipped and
  the first untrusted one is the client. A missing header, a chain of trusted
  entries only, or an entry that is not an IP address falls back to a trusted
  address, never to a client-chosen string. IPv6 clients are keyed by their
  `/64`. Uvicorn runs with `--no-proxy-headers`, so the app is the only place
  that reads the header. Keys are stored only as SHA-256 hashes; an hourly job
  deletes windows that ended more than an hour ago.
- **Applying a policy to a new endpoint** (#29, #30, #169): declare
  `rate_limit("<policy>", ...)` on the route (`dependencies=[...]` or
  `limiter: Annotated[RateLimiter, rate_limit(...)]`), pass
  `responses=RATE_LIMIT_RESPONSES`, add `rate_limit = ["<policy>", ...]` to the
  route's entry in `apps/api/tests/authz/matrix.toml`, and remove the policy
  from `PENDING_POLICIES` in `app/core/ratelimit.py`. For a `failures` policy,
  call `await limiter.reserve("<policy>", key)` before the password check and
  `await reservation.release(session)` on success; the request's transaction
  must be ended first (one connection per request at a time). Every unsafe
  `public()` route must declare a policy or carry `rate_limit_exempt =
  "<reason>"` in the matrix.

### Invitations

Implemented by M2-11 (#29; spec §9, ADR-0007 D4, D10, D11 as amended by #29)
in `apps/api/app/modules/identity/` (`tokens.py`, `invitations.py`,
`invitations_router.py`). No email is sent.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/invitations` | `require("user.invite")` | Body `{username, display_name, role}` (one role). `201` with the one-time `invitation_url`. Staff roles need a recent reauthentication. |
| `GET /api/v1/invitations` | `require("user.invite")` | Open invitations (pending users without a password who have a link), latest link first; offset pagination. No token or hash. |
| `POST /api/v1/invitations/{user_id}/reissue` | `require("user.invite")` | A new link replaces the old one (`200`). `404` unless the user is an open invitation. |
| `POST /api/v1/auth/invitation/lookup` | `public()` | Body `{token}`: the `username`, `display_name`, `roles` and `expires_at` of a valid link. Changes nothing. |
| `POST /api/v1/auth/invitation/redeem` | `public()` | Body `{token, password}`: sets the password and activates the user (`204`). Creates no session. |

- **The token** is 32 random bytes, shown **once** as unpadded base64url in
  `invitation_url` = `APP_ORIGIN + "/invite#token=<token>"`. It travels in the
  URL fragment, so it never reaches a server log, a proxy log or a `Referer`;
  the web page sends it to the API in request bodies only. Only its SHA-256
  hash is stored (`account_tokens`); the token is never logged, audited or
  returned again.
- **Lifetime and replacement:** valid for 24 hours (`now < expires_at`).
  Issuing a new link (reissue) replaces every earlier unused link of the user.
  A link is used once: redemption marks it used in the same transaction that
  sets the password and activates the user. A password-policy failure
  (`422`) leaves the link usable.
- **`410 token_invalid`** for every invalid link: unknown, malformed, used,
  replaced, expired, or a user who is no longer pending without a password
  (for example archived). The body is the same in every case.
- **Who may invite which role:** an invitation that carries a role counts as
  assigning it (ADR-0007 D4): `403 permission_denied` unless the caller may
  assign the role (Owner: `user.owner_role.assign`; Administrator:
  `user.admin_role.assign`; the others: `user.role.assign`). Reissue checks
  every role the user holds. A staff role (Owner, Administrator, Academic
  coordinator, Finance staff) then needs a password reauthentication in the
  last 5 minutes (`recent_auth_guard("user.invitation.staff_role")`, `403
  reauth_required`); Tutor, Student and Parent/Guardian never do.
- **Order on create:** `401` → `403 csrf_failed` → `403 permission_denied`
  (no `user.invite`) → `422` (body, then `username_invalid`) → `403
  permission_denied` (role) → `403 reauth_required` → `409 username_taken`.
- **Order on the public routes:** a foreign `Origin` (`403 csrf_failed`, never
  counted) → `token_redemption.ip` (`429`, 20 per IP per 15 minutes, shared by
  lookup and redeem) → body validation (`422`) → the token (`410`). A `429`
  reveals nothing about the token.
- **Audit:** `identity.invitation_issued` (actor: the admin; `details`:
  `link_id`, `roles`, `expires_at`, `reissued`) and
  `identity.invitation_redeemed` (no actor; `details`: `link_id`), plus the
  automatic `users` and `user_roles` events. `link_id` is the id of the
  `account_tokens` row, never the token or its hash. Failed requests write no
  event.

### Registration requests

Implemented by M2-11b (#169; spec §9, ADR-0007 D6, D7, D13) in
`apps/api/app/modules/identity/` (`registrations.py`,
`registrations_router.py`). No email is sent.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/auth/registrations` | `public()` | Body `{username, display_name, password, role}`, where `role` is `guardian` or `student` (anything else is `422 validation_error`). `202` with `{"status": "pending"}` and no ids. |
| `GET /api/v1/registration-requests` | `require("registration.review")` | Query `status` (`pending` by default, `approved` or `rejected`); oldest first (`created_at`, then `id`); offset pagination. Items: `id`, `username`, `display_name`, `requested_role`, `status`, `submitted_at`, `decided_at`. No password data. |
| `POST /api/v1/registration-requests/{request_id}/approve` | `require("registration.review")` | `200` with the item: the account becomes `active` with the requested role. |
| `POST /api/v1/registration-requests/{request_id}/reject` | `require("registration.review")` | `200` with the item: the account is archived and its password hash is cleared. |

- **Only Parent/Guardian or Student** can be requested; staff and tutors join
  only by invitation. A request creates a `pending` user with the password's
  Argon2id hash and **no role**, and a `pending` row in
  `registration_requests`. A pending account cannot sign in (`401
  invalid_credentials`, like any account that is not `active`).
- **Order on submit:** a foreign `Origin` (`403 csrf_failed`, never counted)
  → `registration.ip` (`429`, 10 per IP per hour) → body validation (`422`)
  → `username_invalid` (`422`) → `409 username_taken` (any user in any state,
  also a concurrent request for the same username) → the password policy
  (`422`). Every request counts against the hourly budget, including `409`
  and `422` answers, which bounds username probing.
- **Approve and reject:** an Owner or Administrator confirms the person's
  identity out of band first. The request is locked: an unknown id is `404
  not_found`; a request that is no longer `pending`, or whose account is no
  longer `pending`, is `409 registration_already_decided` (of two concurrent
  decisions exactly one succeeds). Approval also needs the authority to
  assign the requested role (`403 permission_denied`; both reviewing roles
  have it), then adds the role and moves the account `pending → active`.
  Rejection moves it `pending → archived` and clears the hash; the username
  stays taken. Neither needs a reauthentication (ADR-0007 D10: approval
  grants only Parent/Guardian or Student). The request records `decided_at`
  and `decided_by_user_id`.
- **Registration never links or enrolls anyone** (spec §9): it changes only
  `users`, `user_roles`, `registration_requests`, `audit_events` and
  `rate_limit_counters`. Linking a guardian to a student and enrollment stay
  administrator actions.
- **Audit:** `identity.registration_requested` (no actor),
  `identity.registration_approved` and `identity.registration_rejected` (the
  reviewer), each on the request (`record_type` `registration_requests`) with
  `details` `user_id` and `requested_role`, plus the automatic `users`,
  `user_roles` and `registration_requests` events. No event holds the
  password or its hash. `404`, `409`, `422` and `429` write no event.

### Password reset and change

Implemented by M2-12 (#30; spec §1, §14, ADR-0007 D8, D10, D11, D12, D14) in
`apps/api/app/modules/identity/` (`recovery.py`, `recovery_router.py`, the
`/auth/password` route in `router.py`) and `apps/api/app/cli.py`. No email is
sent, and staff never see or choose a password: a unit test fails if a request
body with a `password` field appears on any route other than login,
reauthentication, the change and the two redemptions.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/password-resets` | `require("user.password_reset.issue")` + `require_recent_auth("user.password_reset.issue")` | Body `{username}`. `201` with the one-time `reset_url`. |
| `POST /api/v1/auth/password-reset/lookup` | `public()` | Body `{token}`: the `username`, `display_name` and `expires_at` of a valid link. Changes nothing. |
| `POST /api/v1/auth/password-reset/redeem` | `public()` | Body `{token, password}`: sets the new password and ends every session of the account (`204`). Creates no session. |
| `POST /api/v1/auth/password` | `authenticated()` | Body `{current_password, new_password}`: the self-service change. `200` with the new session's `SessionUser`. |

- **Who resets whom** (ADR-0007 D12): Owners and Administrators hold
  `user.password_reset.issue`, and every issue needs a password
  reauthentication in the last 5 minutes. Only a user holding the `owner`
  **role** may reset an Owner or an Administrator (a delegated permission does
  not count). Nobody resets their own account; they use the change. The
  target must be `active` (`409 user_not_active`): a pending invitation gets a
  new invitation link, a suspended or archived user is reactivated first.
- **Order on issue:** `401` → `403 csrf_failed` → `403 permission_denied` →
  `403 reauth_required` → `422` (body) → `404 not_found` (an invalid or
  unknown username; letters are not case-sensitive) → `403
  permission_denied` (self, or an Owner/Administrator target for a
  non-Owner) → `409 user_not_active`.
- **The link** works like an invitation link (see Invitations):
  `reset_url` = `APP_ORIGIN + "/reset#token=<token>"`, shown once, only the
  SHA-256 hash is stored (`account_tokens`, kind `password_reset`), valid for
  24 hours, and a new reset replaces the earlier link. Every invalid link is
  the same `410 token_invalid`, including an invitation token sent to the reset
  routes (and the reverse) and a user who is no longer `active` (a reset never
  reactivates anyone). The public routes share the `token_redemption.ip`
  budget with invitation redemption, in the same order (`Origin` → limit →
  body → token).
- **Sessions:** issuing a link changes no session. Redemption sets the new
  password and revokes **every** session of the user in the same transaction
  (`identity.sessions_revoked`, reason `password_changed`); a policy failure
  (`422`) changes nothing and leaves the link usable.
- **The self-service change** asks for the current password instead of a
  reauthentication window. Order: `401` → `403 csrf_failed` → `422` (body,
  then the new-password policy, before any attempt is counted) → `reauth.user`
  (`429`, the budget shared with `POST /auth/reauthenticate`; the session is
  unchanged) → exactly one password check (`403 current_password_invalid`,
  the session stays signed in). On success every session of the user ends,
  the current one included, any live reset link of the user is revoked, and a
  new session (not reauthenticated) replaces the current one: the response
  sets the new cookie and its body carries the new `csrf_token`. The old
  cookie and the old CSRF token stop working at once.
- **Owner recovery from the host shell:** `make reset-owner-password
  USERNAME=<name>` (`python -m app.cli reset-owner-password --username
  <name>`, direct database connection) prints a reset link and its expiry for
  an existing **active** Owner. It refuses (exit 1, nothing written) an
  unknown username, a user without the `owner` role, a non-active Owner and a
  database that is not at the Alembic head. It runs in every environment.
- **Audit:** `identity.password_reset_issued` (actor: the staff member, or
  none from the shell; `details`: `link_id`, `expires_at`, `source` `staff` or
  `cli`), `identity.password_reset_redeemed` (no actor; `link_id`),
  `identity.password_changed` (the user; the new `auth_session_id`) and
  `identity.password_change_failed` (the user; the current `auth_session_id`
  and `reason`, committed even though the request fails). No event holds a
  password, token or hash, and the password hash is excluded from automatic
  `users` events. `404`, `409`, `410`, `422` and `429` answers write no event.

### User administration

Implemented by M2-13 (#31; spec §2, §16, ADR-0007 D4, D7, D8, D10) in
`apps/api/app/modules/identity/` (`user_admin.py`, `user_admin_router.py`; the
last-active-Owner guard in `service.py`).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/users` | `require("user.view")` | Query `q` (at most 100 characters, trimmed), `role` (one role code), `state` (one state), offset pagination. Items: `id`, `username`, `display_name`, `state`, `roles`, `created_at`; no contact fields, password data or delegations. |
| `GET /api/v1/users/{user_id}` | `require("user.view")` | The list fields plus `email`, `phone`, `version`, `updated_at`, `delegations`, `password_set` and the caller's `allowed_actions`. |
| `PATCH /api/v1/users/{user_id}` | `require("user.manage")` | Body `{version, display_name?, email?, phone?}`. |
| `POST /api/v1/users/{user_id}/suspend` | `require("user.manage")` + `require_recent_auth("user.suspend_or_archive")` | Body `{version}`. |
| `POST /api/v1/users/{user_id}/archive` | `require("user.manage")` + `require_recent_auth("user.suspend_or_archive")` | Body `{version}`. |
| `POST /api/v1/users/{user_id}/reactivate` | `require("user.manage")` | Body `{version}`. |

- **Search:** `q` matches part of the username or the display name, not
  case-sensitive; `%`, `_` and `\` match literally. Accents are not folded
  ("jose" doesn't find "José"). Sorted by display name (not case-sensitive),
  then username, then id. Pending invitations and registration requests are
  listed too; `password_set` on the detail tells them apart.
- **Who may manage whom** (ADR-0007 D4 as amended by #31): a holder of
  `user.manage` may edit, suspend, archive or reactivate a user only when they
  may assign **every** role that user holds. Owners manage everyone;
  Administrators manage Academic Coordinators, Finance Staff, Tutors,
  Students, Parents/Guardians and users without roles, and other
  Administrators only with the `user.admin_role.assign` delegation. Everyone
  may edit their own profile; nobody suspends, archives or reactivates
  themselves (`403 permission_denied`). `allowed_actions` is computed with the
  same rules (and the #30 rule for `reset_password`); the routes stay the
  authority.
- **Profile:** fields not sent stay unchanged; `""` or `null` clears `email`
  or `phone`. Patterns (also CHECKs in the database): email
  `^[^@\s]+@[^@\s]+\.[^@\s]+$` (at most 254), phone
  `^\+?[0-9][0-9 ().-]{5,30}[0-9]$` (at most 32); neither is unique. Contact
  fields are **never used to sign in** (ADR-0007 D7) and the username never
  changes. A body that changes nothing returns `200` without a new version.
  Order: `422` → `404` → `403` → `409 version_conflict`.
- **Transitions:** suspend `active → suspended`; archive `active` or
  `suspended → archived`, and `pending → archived` only for an open
  invitation (no password; its link then answers `410`); reactivate
  `suspended` or `archived → active`. Anything else, including a registration
  request (decided on its own page) and `pending → active`, is `409
  user_state_transition_invalid`. Suspending and archiving end every session
  of the user (`identity.sessions_revoked`); reactivation revives no session
  and never touches the password (a user without one needs a reset link).
  Order: `401` → `403 csrf_failed` → `403 permission_denied` → `403
  reauth_required` (suspend, archive) → `422` → `404` → `403
  permission_denied` (may not manage, or own account) → `409
  version_conflict` → `409 user_state_transition_invalid` → `409
  last_active_owner`.
- **The last active Owner** can never be suspended, archived or lose the
  `owner` role. The guard is inside the identity service
  (`change_user_state`, `remove_user_role`), behind a transaction-scoped
  advisory lock, so every caller gets it and two concurrent changes cannot
  both pass; routes never repeat it.
- **Audit:** automatic only (`users` `update` with the changed fields and the
  state, plus `identity.sessions_revoked`); no named event. `403`, `404`,
  `409`, `422` and a body that changes nothing write no event.

### Response headers

Every API response, errors included, carries exactly one
`Cache-Control: no-store` and one `X-Content-Type-Options: nosniff`
(`apps/api/app/core/security_headers.py`); a value a route sets is replaced.

## Request IDs

Approved (architecture §4.3 step 2); implemented by M1-09.

- An inbound `X-Request-ID` is accepted when it matches
  `[A-Za-z0-9._-]{1,128}`. Otherwise (missing or invalid) the API generates a
  UUID. An invalid value is never echoed or logged, and the request is not
  rejected.
- Every response carries exactly one `X-Request-ID` header, and every log line
  written while handling the request carries the same ID.

## Pagination

Approved (spec §10 "Pagination", architecture §4.4); helpers implemented by
M1-09 in `apps/api/app/core/pagination.py`.

- **Offset** for ordinary tables: query `limit` (default 25, 1 to 100) and
  `offset` (0 or more). Response `OffsetPage`: `items`, `total`, `limit`,
  `offset`.
- **Cursor** for large histories (audit, ledger, notifications): query `limit`
  (default 25, 1 to 100) and an opaque `cursor`. Response `CursorPage`: `items`
  and `next_cursor` (`null` on the last page). An invalid cursor returns
  `400 invalid_cursor`. Clients never build or parse cursors.

## Versions

1. **API version:** the path prefix `/api/v1`. A breaking change gets a new
   prefix. Approved (spec §10 "API versioning").
2. **Record version (optimistic concurrency):** detail responses include an
   integer `version`; every update request body sends back the `version` the
   client loaded; a mismatch returns `409 version_conflict` and changes
   nothing. Approved (architecture ISSUE-D5), implemented by M2-13 (`users`:
   `PATCH /api/v1/users/{user_id}` and the state routes). The
   `version_conflict` error exists since M1-09.

## Idempotency

Financial POST endpoints (payments, allocations, refunds, payroll approval and
payment) require an `Idempotency-Key` header. The frontend generates one key
per form submission and reuses it on retry. A replay with the same key and body
returns the stored response; the same key with a different body returns `422`;
a missing key returns `400 idempotency_key_required`. PROPOSED (decided by
architecture ISSUE-F4 / ADR-0006, implemented by M6-03).

## Money

Money is a JSON string such as `"1250.00"` (MXN), described in OpenAPI as
`type: string, format: decimal`. It is never a float on the server, in the
client or in the UI; the UI formats it with
`Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' })`.
Approved (ADR-0004). The shared `Money` type is M6-02.

## Client and contract workflow

Approved (architecture ISSUE-X4, decided on issue #12).

- `make api-client` exports the OpenAPI document from the app code (no server,
  database, Docker or `.env`) to `packages/api-client/openapi.json`, then
  generates `packages/api-client/src/generated/schema.ts` with
  `openapi-typescript`. Both files are committed and never edited by hand.
- **Run `make api-client` after any API change and commit the result.**
- The web app calls the API only through `createApiClient()` from
  `@classora/api-client` (`openapi-fetch` typed with the generated `paths`).
  Calls use the page's own origin. The package also exports the
  `ProblemDetails` type for error handling.
- CI's `api-client` job runs `make check-api-client`, which regenerates both
  files and fails if anything under `packages/api-client` differs from the
  commit (including new or deleted files).
- TanStack Query hooks are written by hand in each feature on top of the
  client; no hooks or Zod schemas are generated.
- **Frontend data layer** (`apps/web/src/app/`): query and mutation
  functions wrap every client call in `apiCall(() => api.GET(…))`
  (`api.ts`), which returns `data` or throws an `ApiError` with `code`,
  `status`, `requestId` and `fieldErrors`. A non-Problem response becomes
  `unexpected_response` and a failed request `network_error`; an abort is
  re-thrown as a cancellation. Query keys are arrays,
  `[domain, resource, params]` (for example `['health', 'live']`). Queries
  retry only `network_error` and 5xx responses, at most twice; a 4xx is never
  retried. Mutations never retry, and financial mutations are never
  optimistic. When an API task adds a new `code`, it adds the message key to
  `errors.json` in both `es` and `en` and maps it in `errors.ts`.
- **Session, CSRF and reauthentication in the web app** (#28,
  `apps/web/src/features/auth/`):
  - The session is one TanStack query, `['auth', 'me']` (`GET /auth/me`).
    Both `401` codes resolve to "signed out", never to a query error. It is
    never polled (no `refetchInterval`), so an idle tab does not keep its
    session alive; it refetches on window focus once older than 60 seconds.
  - The CSRF token lives in one module variable (`features/auth/csrf.ts`),
    set from the login and every `/auth/me` response and cleared on every
    sign-out. It is never stored in browser storage, a cookie, the URL, a
    store, a log or the query cache (the cached user has no `csrf_token`).
    One `openapi-fetch` middleware, registered where `api` is created, adds
    `X-CSRF-Token` to `POST`, `PUT`, `PATCH` and `DELETE`, never to `GET`.
  - `apiCall` handles three answers for every caller: a `401`
    `authentication_required` or `session_expired` clears the token and the
    whole query cache once and sends the user to `/login?next=<page>` (with
    the "session expired" notice only for `session_expired`); `403
    csrf_failed` refetches `/auth/me` and retries the request once (never if
    the user changed: then the cache is cleared and the app goes to `/`);
    `403 reauth_required` opens the password dialog and retries the request
    once after a confirmed password (cancel rejects with the original error).
    A second `csrf_failed` or `reauth_required` reaches the caller.
  - Login and reauthentication are direct calls, not mutations, so the
    password never enters the TanStack mutation cache. Login and logout clear
    every cached response.
  - Route guards and hidden navigation are UX only; the API checks every
    request (spec §16).
- The local/DEV demo job route (`/api/v1/dev/demo-jobs`) is served only when
  `APP_ENV` is `local` or `dev` and is not part of the exported document.
