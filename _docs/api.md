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
| `privacy_notice_required` | 403 | A signed-in user who hasn't accepted the current privacy-notice version, on any protected route except the notice-exempt ones (see Privacy notice). |
| `privacy_notice_outdated` | 409 | `POST /api/v1/privacy-notice/acceptance` with a version that isn't the current one (older, unknown or not yet published); nothing is written (see Privacy notice). |
| `reauth_required` | 403 | A sensitive action whose session has no password reauthentication in the last 5 minutes; reauthenticate and retry (see Reauthentication). |
| `reauth_failed` | 403 | `POST /api/v1/auth/reauthenticate` with a wrong password (or a user without one). The session stays signed in. |
| `current_password_invalid` | 403 | `POST /api/v1/auth/password` with a wrong current password (or a user without one). The session stays signed in (see Password reset and change). |
| `rate_limited` | 429 | Too many attempts on a rate-limited endpoint (login, reauthentication and the password change, one-time link lookup and redemption, self-registration); a `Retry-After` header gives the seconds to wait. The same body whether or not the account exists (see Rate limits). |
| `token_invalid` | 410 | A one-time link (invitation or password reset) that is unknown, malformed, used, replaced or expired, of the other kind, or that can no longer be redeemed. One body for every case (see Invitations). |
| `username_taken` | 409 | `POST /api/v1/invitations` or `POST /api/v1/auth/registrations` with a username another user already has, in any state. |
| `registration_already_decided` | 409 | Since #40 only the `identity.registration` approval handler's defensive check: approving or rejecting a registration whose own record was already decided, or whose account is no longer `pending` (for example archived meanwhile); the approval request stays `pending` (see Registration requests). |
| `approval_transition_invalid` | 409 | The approval request's status doesn't allow the action: it was already approved, rejected or cancelled, or it waits for the requester's changes (see Approval requests). Of two concurrent decisions, the second gets this. |
| `approval_action_unsupported` | 409 | `request-changes` on an approval type that doesn't support it (registrations don't; see Approval requests). |
| `user_not_active` | 409 | `POST /api/v1/password-resets` for a user who is not `active` (pending, suspended or archived). |
| `last_active_owner` | 409 | Suspending or archiving the only active Owner, or taking the `owner` role from them (see User administration). |
| `name_taken` | 409 | A catalog record (an education level, or a subject within its level), a classroom or a course template would get a name another record already has, ignoring case; inactive records count (see Academic catalog, Classrooms, Course templates). |
| `education_level_has_active_subjects` | 409 | Deactivating an education level that still has an active subject (see Academic catalog). |
| `education_level_inactive` | 409 | Creating or reactivating a subject of an inactive education level (see Academic catalog). |
| `too_many_streams` | 429 | `GET /api/v1/notifications/stream` when the caller already holds 10 open streams in this API process; `Retry-After: 30`. The web app polls meanwhile (see Live notifications). |
| `qualification_exists` | 409 | `POST /api/v1/tutors/{user_id}/qualifications` when the tutor already has a `pending` or `approved` qualification for the subject; also the loser of two concurrent requests (see Tutor qualifications). |
| `subject_inactive` | 409 | `POST /api/v1/tutors/{user_id}/qualifications` for a subject that is inactive or whose education level is inactive (see Tutor qualifications). |
| `qualification_not_approved` | 409 | `POST /api/v1/tutors/{user_id}/qualifications/{qualification_id}/revoke` for a qualification that isn't `approved` (pending, rejected, cancelled or already revoked; see Tutor qualifications). |
| `availability_change_requires_approval` | 409 | A tutor availability change (weekly `PUT`, a time-off `POST`, deleting extra availability) that affects scheduled classes; nothing changes. Clients treat it as "needs approval"; M4-18 turns it into an approval request (see Tutor availability). |
| `period_overlap` | 409 | An academic period that is (or becomes) active would share at least one day with another active period of the same type (see Academic periods and closures). |
| `education_level_unavailable` | 422 | A student profile picks an education level that is unknown or inactive (an already assigned inactive level may be kept); the field error is on `["body","education_level_id"]` (see Students). |
| `emergency_contact_unknown` | 422 | A student `PATCH` sends an emergency contact `id` that is not one of this student's contacts, or the same `id` twice; the field error is on `["body","emergency_contacts",i,"id"]` (see Students). |
| `guardian_account_unavailable` | 422 | A guardian profile's `user_id` is an account that is unknown, archived or doesn't hold the `guardian` role; the field error is on `["body","user_id"]` (see Guardians and links). |
| `guardian_account_in_use` | 409 | A guardian profile's `user_id` is already linked to another profile (see Guardians and links). |
| `guardian_unavailable` | 422 | Linking a guardian profile that is unknown or inactive; the field error is on `["body","guardian_id"]` (see Guardians and links). |
| `guardian_already_linked` | 409 | Linking a guardian who is already linked to this student (see Guardians and links). |
| `family_member_conflict` | 409 | Adding a student or guardian profile who already belongs to another family; remove them from it first (see Families). |
| `family_inactive` | 409 | Adding a member to an inactive family; reactivate it first (see Families). |
| `student_profile_required` | 409 | Activating an account that holds `student` (invitation redemption, registration approval, reactivation, adding the role to an active account) while no student profile is linked to it; nothing changes (see Consent and student logins). |
| `guardian_consent_required` | 409 | Activating a minor's student account, linking an active student account to a minor, or changing the birthdate of a student with an active login to a minor's, without a valid consent from a linked, active guardian; nothing changes. On the birthdate change the field error is on `["body","birthdate"]` (see Consent and student logins). |
| `student_account_unavailable` | 422 | `PUT …/account` names an account that is unknown or archived; the field error is on `["body","user_id"]`. |
| `student_account_in_use` | 409 | `PUT …/account` names an account already linked to another student profile. |
| `consent_method_invalid` | 422 | A consent method the caller may not use (staff: `paper`/`in_person`; a guardian or a student: `in_app`); the field error is on `["body","method"]`. |
| `consent_guardian_invalid` | 422 | A consent naming a guardian who isn't linked to the student or whose profile is inactive; the field error is on `["body","guardian_id"]`. |
| `consent_not_applicable` | 422 | `student_login` for an adult or `guardian_academic_access` for a minor; the field error is on `["body","scope"]`. |
| `consent_already_given` | 409 | An active consent for the same student, guardian and scope already exists. |
| `consent_already_revoked` | 409 | Revoking a consent that was already revoked. |
| `tz_change_requires_migration` | 409 | A business-settings change of the center time zone while scheduled data exists (see Business settings); nothing changes. The body never names the data. |
| `invalid_effective_from` | 422 | A new scheduling policy version whose `effective_from` (now when omitted) is in the past or not later than every existing version; nothing is created (see Scheduling policies). |
| `policy_version_in_effect` | 409 | Deleting a scheduling policy version that is in force or in the past; only an upcoming version can be deleted (see Scheduling policies). |
| `user_state_transition_invalid` | 409 | A suspend, archive or reactivate the user's current state does not allow, including the same state (see User administration). |
| `delegation_forbidden` | 403 | The actor of a delegation grant or revocation is not an active Owner (`user.delegation.manage`); the routes already require that permission, so it guards other callers of the identity service. |
| `permission_not_delegable` | 422 | The identity service was asked to delegate a permission outside the four delegable ones (the route's body enum answers `validation_error` first). |
| `delegation_target_invalid` | 422 | A delegation is granted to a user who doesn't hold `administrator` or is archived (see User administration). Revoking works for any user. |
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
  `403 csrf_failed`; a user who hasn't accepted the current privacy notice
  then gets `403 privacy_notice_required` (except on the notice-exempt routes,
  see Privacy notice); without the permission it is `403 permission_denied`; on
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
    `SessionUser` also carries `center_time_zone` (#37), the center's IANA
    time zone from the business settings: the web app shows instants and
    computes "today" in it.
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
  csrf_failed` → `403 privacy_notice_required` → `403 permission_denied` →
  `403 reauth_required` → `422`,
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

### Privacy notice

Implemented by M2-17b (#174; spec §9, §16) in the identity module
(`privacy.py`, `privacy_router.py`) and `apps/api/app/core/authz.py`.

- **Versions** live in `privacy_notice_versions` (`version`, `published_at`,
  `body_es`, `body_en`). A version is **published by an Alembic migration**
  (owner decision #174 E2 (a)), so it is reviewed in a pull request and the
  same in every environment; a trigger rejects `UPDATE` and `DELETE`, so the
  stored text is exactly what each user accepted. Migration 0017 publishes
  **version 1**, a short placeholder whose first line says it is a draft
  pending legal review; M10-07 (#155) publishes the final wording as a new
  version. There is no admin page.
- **Current version:** the highest `version` whose `published_at` is not in
  the future (application clock), so a migration can ship a version that takes
  effect later. With no published version every user is refused (fail closed).
- **The gate.** Every signed-in user, whatever their roles, must accept the
  current version. Until they do, `authenticated()` and `require()` answer
  `403 privacy_notice_required`, after `csrf_failed` and before
  `permission_denied` (order: `401` → `csrf_failed` →
  `privacy_notice_required` → `permission_denied` → `reauth_required` →
  `422`). `public()` routes are unaffected (login, one-time links,
  registration). The flag is read with the user on **every request**, never
  cached: a new version makes everyone accept again on their next request,
  without signing out.
- **Exempt routes** (`authenticated(notice_exempt=True)`, recorded on the
  route's `AccessRule` and as `notice_exempt = true` in the permission
  matrix): exactly `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`,
  `PATCH /api/v1/auth/me/preferences` (#36: the notice page has the language
  switcher, and the first sign-in fills the account's preferences before the
  notice is accepted), `GET /api/v1/privacy-notice` and
  `POST /api/v1/privacy-notice/acceptance`.
  `require()` has no exemption; the self-service password change and
  reauthentication are not exempt (the user accepts first). Adding an
  exemption is a reviewed matrix change.
- `SessionUser` (login, `/auth/me`, the password change) carries
  `privacy_notice_required` (true until the current version is accepted).
- `GET /api/v1/privacy-notice` → `200 {version, published_at, body_es,
  body_en, accepted_at}`: both texts (plain text, paragraphs separated by blank
  lines; the web app shows the one of its UI language) and the caller's
  acceptance time of this version, or `null`.
- `POST /api/v1/privacy-notice/acceptance` with `{"version": <int>}` (unknown
  fields `422`; needs `Origin` and `X-CSRF-Token`) → `200 {version,
  accepted_at}`. The version must be the current one, the one the client
  showed; otherwise `409 privacy_notice_outdated` and nothing is written.
  Accepting again returns the first `accepted_at` and writes nothing.
- **Audit:** each acceptance is a `create` event of
  `privacy_notice_acceptances` (`user_id`, `notice_version_id`,
  `accepted_at`), with the user as actor and the request's ID. No named event.
- `identity.service.current_privacy_notice()` gives the current version's id
  (#35 stamps consent records with it); `accept_privacy_notice()` and
  `has_accepted_current_privacy_notice()` are the other public functions.
- **Web:** while `privacy_notice_required` is true the route guard sends every
  page to `/privacy-notice?next=<page>` (no navigation items there); any
  `403 privacy_notice_required` from `apiCall` refetches `/auth/me` once.

### Preferences

Implemented by M2-18 (#36; spec §10, §12 `Preference`) in the identity module
(`preferences.py`, the auth router).

- **Stored per account** in `user_preferences` (one row per user): the UI
  `language` (`es` | `en`) and the `theme` (`light` | `dark` | `system`).
  `null`, or no row, means the user never stored the value; a row is written
  on the first save (no backfill). Not audited: a UI preference isn't an
  important record, and saving never touches `users` (no `version` bump, no
  audit event).
- `SessionUser.preferences` (login, `/auth/me`, the password change) →
  `{language, theme}`, each `null` when never stored. Read with one statement
  in the single `SessionUser` builder.
- `PATCH /api/v1/auth/me/preferences`, `authenticated(notice_exempt=True)`:
  body `{language?, theme?}` with at least one field → `200 {language,
  theme}` (both values after the save). Only the fields sent change; the last
  write wins (no `version`, see Versions). `{}`, `null` (a stored value can't
  be cleared), an unknown value or another field is `422 validation_error`
  and writes nothing. Order: `401` → `403 csrf_failed` → `422`; never `403
  privacy_notice_required` (notice-exempt). It changes only the caller's own
  preferences: there is no user id in the path or body. No reauthentication,
  rate limit or permission. Concurrent first saves of one user end with one
  row (`INSERT … ON CONFLICT (user_id) DO UPDATE`).
- **Other modules** read a user's values with
  `identity.service.get_user_preferences()` (each may be `None`).
- **Web client rule:** when the session resolves to a user (login, bootstrap
  `/auth/me`, the password change, a changed user), the account's non-null
  values win over the browser's local ones and are applied before the page
  renders, without a request. A `null` value is filled from what the browser
  shows (one `PATCH` for both, once per user per page load; a failure is
  silent). Later `/auth/me` responses apply only changed values, never while
  a save of that field is pending. A switcher choice while signed in applies
  at once, is stored locally and is saved with only that field; saves run one
  at a time. Signed out, the switchers are local only, and signing out keeps
  the applied language and theme.

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
`registrations_router.py`, `registration_approval.py`); reviewed through the
approval inbox since M3-04 (#40). No email is sent.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/auth/registrations` | `public()` | Body `{username, display_name, password, role}`, where `role` is `guardian` or `student` (anything else is `422 validation_error`). `202` with `{"status": "pending"}` and no ids. |

Staff review requests as **approval requests of type `identity.registration`**
(see Approval requests): `GET /api/v1/approval-requests?request_type=identity.registration`,
then `POST /api/v1/approval-requests/{id}/approve` or `…/reject`. The former
`/api/v1/registration-requests` routes are removed; the web app's
`/registration-requests` redirects to `/approvals?type=identity.registration`.

- **Only Parent/Guardian or Student** can be requested; staff and tutors join
  only by invitation. A request creates a `pending` user with the password's
  Argon2id hash and **no role**, a `pending` row in `registration_requests`
  and, in the same transaction, its `pending` approval request (the
  requester is the new user; the subject is the registration request).
  A pending account cannot sign in (`401 invalid_credentials`, like any
  account that is not `active`).
- **Order on submit:** a foreign `Origin` (`403 csrf_failed`, never counted)
  → `registration.ip` (`429`, 10 per IP per hour) → body validation (`422`)
  → `username_invalid` (`422`) → `409 username_taken` (any user in any state,
  also a concurrent request for the same username) → the password policy
  (`422`). Every request counts against the hourly budget, including `409`
  and `422` answers, which bounds username probing.
- **The `identity.registration` type:** review permission
  `registration.review` (Owner and Administrator), no `request-changes`
  (`409 approval_action_unsupported`), a rejection reason is optional (kept
  on the approval request only). Its summary is `{username, display_name,
  requested_role}`.
- **Approve and reject:** an Owner or Administrator confirms the person's
  identity out of band first. After the approval request's checks, the
  handler locks the registration and its account; a registration that is no
  longer `pending`, or whose account is no longer `pending`, is `409
  registration_already_decided` (defensive). Approval also needs the
  authority to assign the requested role (`403 permission_denied`; both
  reviewing roles have it), then adds the role and moves the account
  `pending → active` (the student-login gate may refuse: `409
  guardian_consent_required` or `student_profile_required`, and the request
  stays `pending`). Rejection moves it `pending → archived` and clears the
  hash; the username stays taken. Neither needs a reauthentication
  (ADR-0007 D10: approval grants only Parent/Guardian or Student). The
  registration records `decided_at` and `decided_by_user_id`, in step with
  the approval request's `resolved_*` (expand-and-contract: a later step may
  drop the duplicated columns).
- **Registration never links or enrolls anyone** (spec §9): it changes only
  `users`, `user_roles`, `registration_requests`, `approval_requests`,
  `audit_events` and `rate_limit_counters`. Linking a guardian to a student
  and enrollment stay administrator actions.
- **Audit:** `identity.registration_requested` (no actor),
  `identity.registration_approved` and `identity.registration_rejected` (the
  reviewer), each on the request (`record_type` `registration_requests`) with
  `details` `user_id` and `requested_role`, plus the automatic `users`,
  `user_roles`, `registration_requests` and `approval_requests` events. No
  event holds the password or its hash. `404`, `409`, `422` and `429` write
  no event.

### Approval requests

Implemented by M3-04 (#40; spec §9 "central administrative approval inbox";
architecture §4.1) in `apps/api/app/modules/administration/` (`models.py`,
`approvals.py`, `approvals_router.py`), exported through
`administration.service`. The full inbox UI (assignment, detail panels) is
M8-01 (#118); the web app has a basic list at `/approvals`.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/approval-requests` | `authenticated()` | Query `status` (`open` by default = `pending` + `changes_requested`, one of the five statuses, or `all`), `request_type` (a registered type; anything else is `422`), `assignee` (`any` by default, `me`, `unassigned`), `limit`/`offset`. Only the types whose review permission the caller holds; a caller with none gets an empty page. Order: priority (`high`, `normal`, `low`), then `created_at`, then `id`. |
| `GET /api/v1/approval-requests/{request_id}` | `authenticated()` | The item; `404 not_found` when missing or not reviewable by the caller. |
| `POST /api/v1/approval-requests/{request_id}/approve` | `authenticated()` | No body. `200` with the updated item. |
| `POST /api/v1/approval-requests/{request_id}/reject` | `authenticated()` | Body `{reason?}`. `200` with the updated item. |
| `POST /api/v1/approval-requests/{request_id}/request-changes` | `authenticated()` | Body `{reason}`. `200` with the updated item. |

- **Model** (`approval_requests`, versioned, audited): `request_type` (a
  dotted code a module registers in code, CHECKed format, at most 64
  characters), `subject_id` (the record in the owning module, no foreign
  key; **one approval request per `(request_type, subject_id)`**: a
  resubmission reuses it), `requester_user_id` (`NULL` for system-raised
  items), `assignee_user_id` (`NULL` while unassigned; assignment is
  M8-01), `status`, `priority` (`low`, `normal`, `high`), `resolved_at` and
  `resolved_by_user_id` (set for the final states; a system cancel has no
  resolver), `reason` (at most 1000 characters; required for
  `changes_requested`).
- **State machine:** `pending → approved | rejected | changes_requested |
  cancelled`; `changes_requested → pending` (resubmit) `| rejected |
  cancelled`; `approved`, `rejected` and `cancelled` are final. Anything else
  is `409 approval_transition_invalid`.
- **Visibility:** each type has a **review permission**. The routes are
  `authenticated()`; a request whose type the caller can't review answers
  `404 not_found` exactly like a missing one (the #21 record-policy rule).
- **Decision order:** `401` → `csrf_failed` / `privacy_notice_required` →
  body `422` → the row is locked `FOR UPDATE` → `404` (missing, unregistered
  type, or no review permission) → `409 approval_action_unsupported` → `409
  approval_transition_invalid` → `422 validation_error` on `["body",
  "reason"]` (required for `request-changes`, and for `reject` when the type
  says so; trimmed, at most 1000 characters) → the type's handler → status,
  `resolved_*`, `reason` and `version`. The handler runs in the **same Unit
  of Work**: any error it raises (with its own code, for example `409
  guardian_consent_required`) rolls everything back and the request stays as
  it was. Of two concurrent decisions exactly one succeeds; the other gets
  `409 approval_transition_invalid`.
- **Item** `ApprovalRequestItem`: `id`, `request_type`, `status`,
  `priority`, `requester`, `assignee` and `resolved_by` (`{id,
  display_name}` or `null`), `created_at`, `resolved_at`, `reason`,
  `subject` (the type's summary object; never a secret), `allowed_actions`
  (from `approve`, `reject`, `request_changes`: what the status and the type
  allow now), `version`. A page takes at most `3 + types on the page`
  statements.
- **Audit:** the automatic `create` event when a request is raised, and an
  `update` event with `status`, `resolved_*` and `reason` on each transition
  (the actor is the reviewer). `404`, `409` and `422` write nothing.
- **Types today:** `identity.registration` (see Registration requests) and
  `staff.qualification` (#44, see Tutor qualifications).

**Adding a type** (later issues: M4 offers, session and availability
requests):

1. Implement `administration.service.ApprovalHandler` in the owning module:
   `request_type`, `review_permission`, `supports_request_changes`,
   `reason_required_on_reject`, and async `approve(session, request,
   principal)`, `reject(…, reason)`, `request_changes(…, reason)` and
   `summaries(session, subject_ids)` (display data by subject id, a fixed
   number of statements). The actions receive a read-only
   `ApprovalRequestView`, lock their own subject, raise an `AppError` to
   refuse and never commit.
2. Register it in `app/wiring.py` (`register_approval_wiring`, called by
   `build_app`, `app.cli` and `app.worker`) and extend the unit test that
   pins `registered_approval_types()`.
3. Raise requests with `create_approval_request(session, request_type=…,
   subject_id=…, requester_user_id=…)` in the owning transaction. Requester
   actions use `cancel_approval_request` and `resubmit_approval_request`
   behind the type's own routes (there is no generic requester route);
   `approval_request_id_for_subject` finds the request of a subject.
4. Web: add the review permission to `APPROVAL_REVIEW_PERMISSIONS`, a label
   to `APPROVAL_TYPE_LABELS`, a summary renderer in
   `features/approvals/summary.ts` and, when a rejection needs a reason, the
   type to `REJECT_REASON_REQUIRED`.

`administration` imports no other module: names come from the
account-reference resolver that the composition roots register
(`register_account_reference_resolver(identity.service.get_account_references)`);
without one, building an item fails closed (`500`).

### Notifications

Implemented by M3-05 (#41; spec §1, §9: in-app only; ISSUE-X1) in
`apps/api/app/modules/administration/` (`models.py`, `notifications.py`,
`notifications_router.py`, `notifications_dev.py`), exported through
`administration.service`. Live delivery (SSE) is M3-06 (#42, below);
preferences are M8-02 (#119); retention is M8-12 (#129).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/notifications` | `authenticated()` | The caller's own notifications, newest first by `(created_at, id)`. Cursor pagination (`limit`, `cursor`; see Pagination); `unread_only` (default `false`). An invalid cursor is `400 invalid_cursor`; `403 privacy_notice_required` before the notice is accepted. |
| `GET /api/v1/notifications/unread-count` | `authenticated()` | `{"count": n}` (one statement on a partial index). `403 privacy_notice_required` before the notice is accepted. |
| `POST /api/v1/notifications/{notification_id}/read` | `authenticated()` | No body. `200` with the item. Idempotent: `read_at` keeps its first value. `403 csrf_failed` or `403 privacy_notice_required` before the notice is accepted; `404 not_found` for another user's or an unknown id. |
| `POST /api/v1/notifications/read-all` | `authenticated()` | Body `{"up_to": <id> \| null}` (`extra="forbid"`; the body may be left out). `200` with `{"updated": n}`. `403 csrf_failed` or `403 privacy_notice_required` before the notice is accepted; `404 not_found` when `up_to` isn't the caller's. |
| `POST /api/v1/dev/notifications` | `authenticated()` | **Local/DEV only** (mounted with `/api/v1/dev/demo-jobs`, never in the exported contract). Body `{"label"}` (1–100 characters). Creates one `dev.demo` notification for the caller; `201` with the item. |

- **Model** (`notifications`; neither audited nor versioned: per-user
  messages, and the action behind each one is audited where it happens):
  `recipient_user_id` (`users`, `ON DELETE RESTRICT`), `notification_type`
  (a dotted code, CHECKed format, at most 64 characters), `params` (a JSONB
  object, default `{}`), `read_at` (`NULL` while unread).
- **`{type, params}`:** the client renders the message from the i18n key
  `notifications.types.<type with "." replaced by "_">` in `common.json`,
  interpolating `params`, so a language switch applies to old
  notifications too. An unknown type shows a generic message, never its
  code.
- **Params rules** (checked by `create_notification` after the type's
  model): a flat object of strings (at most 200 characters), integers,
  booleans or `null`; at most 2 KB as JSON; no key matching the sensitive
  pattern (`password|token|secret|hash`). Values are data (names, dates as
  `YYYY-MM-DD`, codes), never translated text; the client translates codes.
- **Own rows only:** a user lists, counts and marks only their own
  notifications. Another user's id, or an unknown one, answers `404
  not_found` with the same body (`read`, and `read-all`'s `up_to`).
- **`read-all` and `up_to`:** marks the caller's unread notifications at or
  before `up_to`'s `(created_at, id)` position (all of them when `null`), so
  one that arrives while the panel is open stays unread. The web app sends
  the newest id it shows.
- **Recipients** may be in any account state; the notification waits until
  they can sign in.

**Adding a type** (later issues: #44 qualification decisions, M4 offers and
reminders):

1. Define a Pydantic model of the params and register it with
   `administration.service.register_notification_type("module.event",
   Model)` in `app/wiring.py` (called by `build_app`, `app.cli` and
   `app.worker`). Registering the same model again changes nothing; another
   model for a registered code raises.
2. Create notifications with `create_notification(session,
   recipient_user_id=…, notification_type=…, params=Model(...))` **inside
   the transaction of the action that causes them**. It validates the
   params, adds the row, flushes and issues the live `NOTIFY` (delivered
   only if the transaction commits); it never commits, so a rollback leaves
   no notification and no event. An unknown type or bad params raise
   `ValueError` (a programming error).
3. Web: add `notifications.types.<module_event>` to both `common.json`
   catalogs and, when the notification opens a page, an entry in
   `NOTIFICATION_LINKS` (`features/notifications/messages.ts`).

### Live notifications

Implemented by M3-06 (#42; spec §10 SSE and `LISTEN`/`NOTIFY`, architecture
§7.2 and ISSUE-N1, ADR-0003) in `administration/live.py` and
`notifications_router.py`, with `app/core/sse.py`.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/notifications/stream` | `authenticated()` | `text/event-stream` of the caller's new notifications. `401`, `403 privacy_notice_required`, `429 too_many_streams` (with `Retry-After: 30`) as Problem Details. Optional `Last-Event-ID` header. |

- **Delivery:** `create_notification` issues `pg_notify('notifications',
  '{"user_id": …, "notification_id": …}')` in the creating transaction, so an
  event exists only after the commit. Each API process holds **one direct
  `LISTEN` connection** (`DATABASE_DIRECT_URL`, never PgBouncer; started by
  the app lifespan) and hands each event to the recipient's open streams in
  that process only. Nothing but ids travels: the client refetches through
  the routes above.
- **Events:** first `retry: 5000` and a comment (so proxies flush), then
  `event: notification` with `id: <notification_id>` and `data:
  {"notification_id": "<uuid>"}`, `event: resync` with `data: {}` and no
  `id` (refetch everything: events may have been missed), and a `:
  heartbeat` comment every 15 s.
- **`Last-Event-ID`** (the browser sends it when it reconnects by itself):
  when it names one of the caller's notifications, the caller's notifications
  created after it (by `(created_at, id)`) come first, oldest first, up to
  100. More than 100, an unknown id, another user's id or a value that isn't
  a UUID give one `resync` instead (the answer never tells which).
- **`resync`** is also sent to every stream after the server's `LISTEN`
  connection reconnects (backoff 1, 2, 4 … 30 s; the API keeps serving while
  the database is down), and to a stream whose 100-event buffer overflowed.
- **Sessions:** the stream doesn't keep a session alive. Opening it never
  refreshes `last_seen_at`, and neither does the stream, so the idle
  timeouts apply as if the tab were idle. Every 60 s the stream re-checks its
  session read-only and ends when it was logged out, revoked or timed out (or
  the user is no longer active).
- **Limits:** at most 10 open streams per user per API process (`429
  too_many_streams`). Streams end at shutdown; the API container stops
  uvicorn with a 5 s graceful timeout so open streams never hang a stop.
- **Headers:** `Cache-Control: no-store` (as every API response),
  `X-Accel-Buffering: no`. A proxy in front of the API must not buffer or
  compress this route (Caddy: M9-09 #139; CloudFront timeouts above the 15 s
  heartbeat: M9-10 #140). The Vite dev proxy streams it unchanged.
- **Web fallback:** the bell opens one `EventSource` per tab (signed in, the
  privacy notice accepted). While it is open the 30 s unread-count polling is
  off; while the browser reconnects by itself, or after the stream closed
  (an HTTP error such as `429`), polling runs, and after a close a new stream
  is tried every 60 s. Without `EventSource` the bell only polls.

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

Implemented by M2-13 (#31) and M2-13b (#170; roles and delegations; spec §2,
§16, ADR-0007 D4, D7, D8, D10) in `apps/api/app/modules/identity/`
(`user_admin.py`, `user_admin_router.py`; the last-active-Owner guard and the
delegation rules in `service.py`).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/users` | `require("user.view")` | Query `q` (at most 100 characters, trimmed), `role` (one role code), `state` (one state), offset pagination. Items: `id`, `username`, `display_name`, `state`, `roles`, `created_at`; no contact fields, password data or delegations. |
| `GET /api/v1/users/{user_id}` | `require("user.view")` | The list fields plus `email`, `phone`, `version`, `updated_at`, `delegations`, `password_set` and the caller's `allowed_actions`. |
| `PATCH /api/v1/users/{user_id}` | `require("user.manage")` | Body `{version, display_name?, email?, phone?}`. |
| `POST /api/v1/users/{user_id}/suspend` | `require("user.manage")` + `require_recent_auth("user.suspend_or_archive")` | Body `{version}`. |
| `POST /api/v1/users/{user_id}/archive` | `require("user.manage")` + `require_recent_auth("user.suspend_or_archive")` | Body `{version}`. |
| `POST /api/v1/users/{user_id}/reactivate` | `require("user.manage")` | Body `{version}`. |
| `PUT /api/v1/users/{user_id}/roles` | `require("user.role.assign")` + `require_recent_auth("user.roles.change")` | Body `{roles, version}`: the complete set, 1–7 distinct role codes. |
| `PUT /api/v1/users/{user_id}/delegations` | `require("user.delegation.manage")` (Owners only) + `require_recent_auth("user.delegations.change")` | Body `{permissions, version}`: the complete set, 0–4 distinct delegable codes. |

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
- **Roles** (#170): who may assign which role follows ADR-0007 D4: Owners
  every role; Administrators every role except Administrator (only with the
  `user.admin_role.assign` delegation) and Owner (never). Removing a role
  needs the same authority as adding it, and the caller must also be allowed
  to manage the user (above), so an Administrator changes no role of an
  Owner. Roles change in every state (an open invitation then grants the new
  set). Added roles are applied first, then removed ones; any change ends
  every session of the user, **including the caller's own when they change
  their own roles** (the response is still `200`; the web app signs out
  locally and shows a notice on the login page). Removing `owner` from the
  only active Owner is `409 last_active_owner` and nothing changes. Removing
  `administrator` also removes all of that user's delegated permissions.
  `allowed_actions.assignable_roles` lists the roles the caller may add and
  remove on this user. Order: `401` → `403 csrf_failed` → `403
  permission_denied` → `403 reauth_required` → `422` → `404` → `403
  permission_denied` (may not manage the user, or may not assign a role
  added or removed) → `409 version_conflict` → `409 last_active_owner`.
- **Delegated permissions** (#170): only an Owner grants or revokes the four
  delegable permissions. A grant needs a user who holds `administrator` and
  isn't archived (`422 delegation_target_invalid`); revoking works for any
  user. A change **doesn't end the Administrator's sessions**: their next
  request uses the new permissions. `allowed_actions.manage_delegations` is
  true for an Owner looking at an Administrator. Order: `401` → `403
  csrf_failed` → `403 permission_denied` → `403 reauth_required` → `422` →
  `404` → `409 version_conflict` → `422 delegation_target_invalid`.
- **Versions** (#170): a role or delegation change increments the user's
  `version` (no `users` audit event: only `version`/`updated_at` change); of
  two concurrent changes from one version the second gets `409
  version_conflict`. The same set as today returns `200` and changes
  nothing.
- **Audit:** automatic only (`users` `update` with the changed fields and the
  state, `user_roles` and `user_delegations` `create`/`delete`, plus
  `identity.sessions_revoked`); no named event. `403`, `404`, `409`, `422`
  and a body that changes nothing write no event.

### Business settings

Implemented by M3-01 (#37; spec §9, §16; ADR-0004) in
`apps/api/app/modules/administration/` (`settings.py`, `settings_router.py`).
One record for the center: its name and contact details, the weekly
operating hours and the **center time zone**.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/business-settings` | `authenticated()` | `BusinessSettingsDetail`: `center_name`, `contact_phone`, `contact_email`, `address`, `time_zone`, `operating_hours` (`[{weekday, opens_at, closes_at}]` by weekday, times `HH:MM`), `version`, `updated_at`. Every role reads it: nothing in it is personal data. |
| `PATCH /api/v1/business-settings` | `require("settings.manage")` + `recent_auth_guard("settings.payment_methods_or_time_zone")` | Body `{version, center_name?, contact_phone?, contact_email?, address?, time_zone?, operating_hours?}`. |

- **Singleton:** exactly one row, inserted by migration 0020 (`Classora`, no
  contact details, `America/Mexico_City`, `version` 1) with the default hours
  Monday to Friday 09:00-20:00 and Saturday 09:00-14:00 (Sunday closed). The
  database allows no second row. `make seed` never touches it.
- **Permission:** `settings.manage` (Owners and Administrators; not
  delegable).
- **Fields:** fields not sent stay unchanged. `center_name` is trimmed, 1-100
  characters; `null` is `422`. `contact_phone`, `contact_email` (the
  user-profile patterns, see User administration) and `address` (up to 300
  characters) are trimmed; `null` or `""` clears them. `time_zone` is any
  IANA key in the system zone data (`zoneinfo`); the web app offers Mexico's
  zones; `null` is `422`.
- **Operating hours:** local wall-clock times in the center's zone, one
  interval per ISO weekday (1 = Monday … 7 = Sunday); a weekday that isn't
  listed is **closed**. `operating_hours`, when sent, **replaces the whole
  week**: 0-7 entries, each weekday at most once, `opens_at < closes_at`,
  `HH:MM` times (`422` on the field otherwise; `null` is `422`). Only the
  weekdays that changed are written. Split shifts and holiday hours are not
  supported (closures are a separate record, M3-02).
- **Reauthentication:** a `time_zone` that **differs** from the stored one
  needs a recent reauthentication (`403 reauth_required`, ADR-0007 D10;
  sensitive action `settings.payment_methods_or_time_zone`, which M8-04 also
  applies to payment methods). Sending the current zone needs none.
- **Check order:** `401` → `403 csrf_failed` → `403 privacy_notice_required`
  → `403 permission_denied` → `422` → `403 reauth_required` (time-zone
  change only) → `409 version_conflict` (the row is locked `FOR UPDATE`) →
  `409 tz_change_requires_migration` (time-zone change only) → the update.
  Any change, hours only included, bumps `version` once; a body that changes
  nothing returns `200` with no new version and no audit event.
- **Time-zone guard.** *Scheduled data* means rows whose stored **instants**
  were computed from the center's local time: class sessions and recurrence
  series (owner-delegated decision on #37, E1 (a)). Rows that hold only
  calendar dates or local wall-clock times (academic periods, closures,
  classroom and tutor availability, operating hours) keep their meaning
  when the zone changes (ADR-0004) and don't count. A module that owns
  scheduled data registers a check when the app is built:
  `administration.service.register_time_zone_dependency(name, has_data)`,
  where `has_data(session)` returns whether it holds any; a name registered
  twice with a different check raises. A time-zone change runs every check
  under the settings row lock and, if any returns true, answers `409
  tz_change_requires_migration` and changes nothing. M4-02 (#47) registers
  `scheduling.class_sessions`: any class session, in any state (past and
  cancelled ones included), blocks a zone change. M4-08 (#53) adds a check
  for recurrence series. Both read the zone with
  `administration.service.get_center_time_zone(session, for_share=True)`
  (`FOR SHARE` on the settings row) while they create sessions, so a
  concurrent zone change can't slip in. The migration job that would move
  existing instants to a new zone has no issue yet.
- **Reading the zone elsewhere:** `administration.service.get_center_time_zone(session)`
  and `center_today(session)` (today's date in the center's zone, from the
  application clock), cached in the Unit of Work (one statement per
  request). `app.core.clock.today_in(zone)` is the pure helper;
  `DEFAULT_CENTER_TIME_ZONE` is only the migration's default.
  `SessionUser.center_time_zone` carries the zone to the web app (see
  Sessions).
- **Audit:** automatic only: `business_settings` `update` with the changed
  fields; `operating_hours` `create`/`update`/`delete` per weekday. An
  hours-only change writes only `operating_hours` events. `403`, `409`,
  `422` and a body that changes nothing write no event.

### Scheduling policies

Implemented by M4-16 (#61; spec §3, §9; ADR-0005 D1, D3, D6) in
`apps/api/app/modules/administration/` (`scheduling_policy.py`, the pure
evaluator; `policy_versions.py`, the versions; `policies_router.py`). One
**effective-dated** policy for the center: the notice windows, the offer
response time and the makeup rules.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/scheduling-policies` | `authenticated()` | `SchedulingPolicyOverview`: `current` (the version in force now), `upcoming` (`effective_from` later than now, oldest first) and `history` (versions no longer in force, newest first, at most 50). Each item: `id`, `effective_from`, the rules below and `created_at` (the author is in the audit trail). Every role reads it: families and tutors need the notice rules, and nothing in it is personal data. |
| `POST /api/v1/scheduling-policies` | `require("settings.manage")` | Body `SchedulingPolicyCreate` (`extra="forbid"`): `based_on_id`, every rule (all required) and an optional aware `effective_from`. `201` with the new version. |
| `DELETE /api/v1/scheduling-policies/{policy_id}` | `require("settings.manage")` | `204` for an upcoming version. |

- **Rules** (ranges inclusive, `422` on the field otherwise):
  `booking_notice_hours` 0-336 (0 = no notice), `change_notice_hours` 0-336
  (0 = a change before the start is never late), `response_hours` 1-168,
  `makeup_after_on_time`, `makeup_after_late`, `makeup_after_no_show`
  (booleans) and `makeup_window_days` 1-365. Version 1, inserted by migration
  0031 and effective from `2000-01-01T00:00:00Z`, holds the defaults 24, 24,
  48, true, false, false, 30, so a version is in force at every instant.
  `make seed` never touches it.
- **Effective dating, append-only:** a version is in force from its unique
  `effective_from` until the next version's. Versions are created, never
  edited, so an event can always be classified again with the version in
  force at the time. To change an upcoming version, delete it and create
  another. A body equal to the latest version is still accepted (an
  explicit re-publish); the web app disables Save when nothing changed.
- **Create order:** `401` → `403 csrf_failed` → `403 privacy_notice_required`
  → `403 permission_denied` → `422` (body) → a transaction-scoped advisory
  lock with one constant key (writers are serialized) → `based_on_id` must
  be the version with the latest `effective_from` (current or upcoming),
  else `409 version_conflict` → `effective_from` (when omitted, the
  application clock's now) must not be in the past and must be later than
  every existing `effective_from`, else `422 invalid_effective_from` → the
  insert.
- **Delete order:** `401` → `403 csrf_failed` → `403 privacy_notice_required`
  → `403 permission_denied` → the same advisory lock → `404` for an unknown
  id → `409 policy_version_in_effect` for a version in force or in the past
  (version 1 always is) → the delete.
- **Not a time-zone dependency** (#37 D): the instants are moments chosen by
  an administrator, not occurrences computed from local time. The web app's
  "From date" sends the start of that date in the center's zone.
- **For other modules** (ADR-0002: import from `administration.service`,
  never the model): `get_scheduling_policy(session, at)` returns the
  `SchedulingPolicyValues` in force at `at` (the latest `effective_from <=
  at`, one statement; a naive `at` raises `ValueError`). The pure evaluator
  takes aware datetimes only (`ValueError` otherwise), reads no clock and no
  zone, and compares instants:
  - `classify_change(policy, *, kind, initiated_by, starts_at, at)` →
    `PolicyOutcome(classification, makeup_allowed, makeup_until,
    policy_id)`. `kind` is `cancellation`, `reschedule` or `absence`;
    `initiated_by` is `family` (a student or guardian), `tutor` or `center`
    (staff or the system); `at` is when the request was made, or when the
    absence was recorded.

    | Case (first match) | Classification |
    | ------------------ | -------------- |
    | `initiated_by` is `tutor` or `center` | `excused` |
    | `kind = absence` | `no_show` |
    | `at >= starts_at` | `no_show` |
    | `at > starts_at - change_notice_hours` | `late` |
    | otherwise (exactly the notice before the start included) | `on_time` |

    Makeup: a `reschedule` never allows one (it is its own replacement);
    otherwise `excused` allows one and `on_time`/`late`/`no_show` follow
    `makeup_after_*`. When allowed, `makeup_until = starts_at +
    makeup_window_days` (days of 24 hours), else `null`. M4-17 (#62) stores
    the outcome at request time and never recomputes it.
  - `violates_booking_notice(policy, *, starts_at, at)`: true when
    `starts_at - at < booking_notice_hours` (exactly the notice is fine;
    with 0, only a start before `at`). The input of the soft `notice_window`
    conflict (#48, ADR-0005 D1) and of slot filtering (#59).
  - `default_respond_by(policy, *, offered_at, first_starts_at)` =
    `min(offered_at + response_hours, first_starts_at)` (#52, ADR-0005 D3).
- **Audit:** automatic only: a `create` event with every value, and a
  `delete` event. `403`, `404`, `409` and `422` write no event. There is no
  `update`.

### Academic catalog

Implemented by M2-14 (#32; spec §4, §12) in `apps/api/app/modules/catalog/`.
Education levels and the subjects taught at each level. A subject belongs to
exactly one level and never moves to another.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/education-levels` | `authenticated()` | Query `status` (`active` (default), `inactive` or `all`), offset pagination. Sorted by `sort_order`, then name (not case-sensitive). |
| `POST /api/v1/education-levels` | `require("catalog.manage")` | Body `{name, sort_order?}`; without `sort_order` the level goes last (the highest + 10, at most 9999). `201`. |
| `GET /api/v1/education-levels/{level_id}` | `authenticated()` | Active or inactive. Fields: `id`, `name`, `sort_order`, `is_active`, `active_subject_count`, `version`, `created_at`, `updated_at`. |
| `PATCH /api/v1/education-levels/{level_id}` | `require("catalog.manage")` | Body `{version, name?, sort_order?, is_active?}`. |
| `POST /api/v1/education-levels/{level_id}/subjects` | `require("catalog.manage")` | Body `{name}`. `201`. |
| `GET /api/v1/subjects` | `authenticated()` | Query `education_level_id` (an unknown id gives an empty page), `status` (as above), offset pagination. Sorted by the level's position and name, then the subject's name. |
| `GET /api/v1/subjects/{subject_id}` | `authenticated()` | Active or inactive. Fields: `id`, `education_level_id`, `education_level_name`, `name`, `is_active`, `version`, `created_at`, `updated_at`. |
| `PATCH /api/v1/subjects/{subject_id}` | `require("catalog.manage")` | Body `{version, name?, is_active?}`; `education_level_id` can't be sent (`422`). |

- **Read for every signed-in user**, students and guardians included: later
  screens offer the catalog in selects. The lists default to `status=active`,
  the values a new record may choose.
- **Deactivation instead of deletion:** nothing is deleted; `is_active:
  false` deactivates and `true` reactivates. Records that already point at a
  deactivated level or subject keep it.
- **An active subject always has an active level:** a level with an active
  subject can't be deactivated (`409 education_level_has_active_subjects`),
  and a subject can't be created or reactivated under an inactive level (`409
  education_level_inactive`). Renaming or deactivating a subject of an
  inactive level is allowed.
- **Names** are trimmed, 1 to 100 characters, free text stored once and shown
  as stored in every language. They are unique ignoring case (not accents):
  level names across all levels, subject names within their level, inactive
  records included (`409 name_taken`). Renaming to another case of the same
  name is allowed.
- **Updates:** fields not sent stay unchanged; `null` and unknown fields are
  `422`. Check order: `422` → `404` → `409 version_conflict` → `409
  name_taken` → `409 education_level_has_active_subjects` /
  `education_level_inactive`. A body that changes nothing returns `200` with
  no new version.
- **Reference data:** `make seed` inserts four example levels (Primaria,
  Secundaria, Preparatoria, Universidad) once; later runs never change or
  restore them.
- **Audit:** automatic only (`education_levels` and `subjects` `create` and
  `update` with the changed fields); no named event. `403`, `404`, `409`,
  `422` and a body that changes nothing write no event.

### Academic periods and closures

Implemented by M3-02 (#38; spec §3, §4; ADR-0004) in
`apps/api/app/modules/catalog/` (`periods.py`, `periods_router.py`).
Academic periods (school years, semesters, terms) and center-wide closures
(holidays, closures, vacations), both as ranges of local calendar dates.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/academic-periods` | `authenticated()` | Query `status` (`active` (default), `inactive`, `all`), `period_type` (`school_year`, `semester`, `term`), `on` (`YYYY-MM-DD`: only periods that contain that date), offset pagination. Sorted by `starts_on` (newest first), then name (not case-sensitive), then `id`. |
| `POST /api/v1/academic-periods` | `require("catalog.manage")` | Body `{name, period_type, starts_on, ends_on}`. `201`. |
| `GET /api/v1/academic-periods/{period_id}` | `authenticated()` | Active or inactive. Fields: `id`, `name`, `period_type`, `starts_on`, `ends_on`, `is_active`, `version`, `created_at`, `updated_at`. |
| `PATCH /api/v1/academic-periods/{period_id}` | `require("catalog.manage")` | Body `{version, name?, starts_on?, ends_on?, is_active?}`; `period_type` can't change (`422`): deactivate the period and add a new one. |
| `GET /api/v1/closures` | `authenticated()` | Query `status` (as above), `closure_type` (`holiday`, `closure`, `vacation`), `from`/`to` (optional dates: only closures that overlap `[from, to]`, inclusive; `from` after `to` is `422` on `to`), offset pagination. Sorted by `starts_on`, `ends_on`, `id`. |
| `POST /api/v1/closures` | `require("catalog.manage")` | Body `{name, closure_type, starts_on, ends_on}`. `201`. |
| `GET /api/v1/closures/check?date=YYYY-MM-DD` | `authenticated()` | `{date, closed, closures}`: the **active** closures that contain the date, in list order. `date` is required (`422` when missing or not a real `YYYY-MM-DD`). |
| `GET /api/v1/closures/{closure_id}` | `authenticated()` | Active or inactive. Fields as a period, with `closure_type`. |
| `PATCH /api/v1/closures/{closure_id}` | `require("catalog.manage")` | Body `{version, name?, closure_type?, starts_on?, ends_on?, is_active?}`. |

- **Dates** are local calendar dates (`DATE`, `YYYY-MM-DD` only: no time, no
  number), never converted between zones. Both `starts_on` and `ends_on` are
  **inclusive**: a one-day closure has `starts_on = ends_on`. `ends_on` is on
  or after `starts_on`, at most 730 days later for a period and 366 for a
  closure (`422` on `ends_on`). A `PATCH` that sends one date is checked
  against the stored one, after `404` and the version: `422
  validation_error` with a field error on `["body","ends_on"]` (code
  `date_range_invalid`).
- **Overlap rule (periods only):** two **active** periods of the **same
  type** never share a day (`409 period_overlap`). Touching ranges (one ends
  on D, the next starts on D+1) are fine; a school year and a semester may
  overlap; inactive periods are ignored, so a wrong period can be
  deactivated and replaced, and reactivating it then gives `409
  period_overlap` if it overlaps. The database enforces the rule with the
  exclusion constraint `ex_academic_periods_no_overlap` (`btree_gist`); the
  service checks first and maps a violation at flush to the same error, so
  concurrent requests give one success and one `409`.
- **Closures may overlap** each other (a holiday inside a vacation is
  normal) and their names repeat. Only active closures close the center.
- **Names:** trimmed, 1 to 100 characters. Period names are unique ignoring
  case, inactive periods included (`409 name_taken`); closure names are not
  unique.
- **Updates:** fields not sent stay unchanged; `null` and unknown fields are
  `422`. Check order: `422` → `404` → `409 version_conflict` → `422` (one
  sent date breaks the stored range) → `409 name_taken` → `409
  period_overlap`. A body that changes nothing returns `200` with no new
  version. Nothing is deleted: `is_active` deactivates and reactivates.
- **Service helpers** for other modules (`catalog.service`, ADR-0002):
  `closures_between(session, start, end)` (active closures that share a day
  with `[start, end]`; `start > end` raises `ValueError`; one statement),
  `is_center_closed_on(session, local_date)` and
  `is_center_closed_at(session, instant)`, which converts an aware instant to
  its local date in the center time zone
  (`administration.service.get_center_time_zone`; a naive `datetime` raises
  `ValueError`). Tutor availability (#45) and scheduling (M4) use them.
- **No reference or demo data:** holidays differ per year and per center.
- **Audit:** automatic only (`academic_periods` and `closures` `create` and
  `update` with the changed fields, dates as `YYYY-MM-DD`); `403`, `404`,
  `409`, `422` and a body that changes nothing write no event.

### Classrooms

Implemented by M3-03 (#39; spec §3; ADR-0004) in
`apps/api/app/modules/catalog/` (`classrooms.py`, `classrooms_router.py`).
A basic room catalog: a name, a capacity, active or not, and weekly
availability windows. No equipment inventory, nothing is deleted. Every
signed-in user can read it (tutors pick preferred rooms, schedules show
rooms); Owners and Administrators maintain it.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/classrooms` | `authenticated()` | Query `status` (`active` (default), `inactive`, `all`), offset pagination. Sorted by name (not case-sensitive), then `id`. At most three statements per page. |
| `POST /api/v1/classrooms` | `require("catalog.manage")` | Body `{name, capacity, availability?}` (`availability` defaults to `[]`). `201` with the detail. |
| `GET /api/v1/classrooms/{classroom_id}` | `authenticated()` | Active or inactive. Fields: `id`, `name`, `capacity`, `is_active`, `availability` (`[{weekday, starts_at, ends_at}]` by weekday, then start; times `HH:MM`), `version`, `created_at`, `updated_at`. |
| `PATCH /api/v1/classrooms/{classroom_id}` | `require("catalog.manage")` | Body `{version, name?, capacity?, is_active?, availability?}`. |

- **Capacity:** a whole number from 1 to 500 (`422`; the database has the
  same CHECK). The upper bound catches typos.
- **Windows:** ISO `weekday` (1 = Monday … 7 = Sunday), `starts_at` and
  `ends_at` as local wall-clock times `HH:MM` (ADR-0004), `starts_at <
  ends_at`, never across midnight. A room has at most 28 windows. Windows
  of one weekday may **touch** (09:00–12:00 and 12:00–14:00) but never
  overlap: `422 validation_error` with a field error on the later
  overlapping item, `["body","availability",<index>,"starts_at"]`, code
  `window_overlap`. The database backs the rule with the exclusion
  constraint `ex_classroom_windows_no_overlap` (`btree_gist`); a violation
  at flush is also `422 validation_error` (on `["body","availability"]`).
- **Semantics:** a room with **no windows has no time restriction**; a room
  with windows is available only inside them (their union: touching windows
  join).
- **Full replacement:** `availability` in a `PATCH` replaces all the room's
  windows (`[]` clears them; `null` is `422`). Only the windows that changed
  are deleted and inserted (a changed window is a delete plus an insert), and
  the order of the list doesn't matter. Any change bumps the room's
  `version` once; the version covers its windows.
- **Names:** trimmed, 1 to 100 characters, unique ignoring case, inactive
  rooms included (`409 name_taken`).
- **Updates:** fields not sent stay unchanged; `null` and unknown fields are
  `422`. Check order: `422` → `404` → `409 version_conflict` → `409
  name_taken`. A body that changes nothing returns `200` with no new version.
- **Service helpers** for other modules (`catalog.service`, ADR-0002):
  `get_classroom(session, classroom_id)` (the detail; `404` when unknown),
  `classroom_references(session, ids)` (`{id: ClassroomReference(id, name,
  is_active)}`, active or not, unknown ids left out; one statement, #197),
  `active_classroom_ids(session, ids)` (which of the ids exist and are
  active; one statement) and `is_classroom_available(session, classroom_id,
  weekday, start, end)`: false for an unknown or inactive room, true for a
  room without windows, otherwise true only if `[start, end)` is covered by
  the union of that weekday's windows (`start >= end` or a weekday outside
  1–7 raises `ValueError`). Preferred rooms (#197) and scheduling (M4) use
  them. Bookings and capacity against sessions are M4's.
- **No reference or demo rooms.**
- **Audit:** automatic only: `classrooms` `create`/`update` with the changed
  fields, and one `classroom_availability_windows` `create` or `delete` per
  window (times as `HH:MM:SS`). A windows-only change writes no `classrooms`
  event. `403`, `404`, `409`, `422` and a body that changes nothing write no
  event.

### Students

Implemented by M2-15 (#33; spec §2, §4, §16) in
`apps/api/app/modules/families/`. A profile for every student, with or
without a login.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/students` | `require("student.view")`, list filtered by record policy | Query `q` (name search, see below), `status` (`active` (default), `inactive` or `all`), `education_level_id` (an unknown id gives an empty page), offset pagination. Items: `id`, `given_names`, `family_names`, `is_active`, `is_minor`, `school_grade`, `education_level` (`{id, name, is_active}` or `null`); never a birthdate or contact data. |
| `POST /api/v1/students` | `require("student.manage")` | Body `{given_names, family_names, birthdate, email?, phone?, school_name?, school_grade?, education_level_id?, emergency_contacts: [{name, relationship, phone}] (1-5)}`. `201` with the detail. |
| `GET /api/v1/students/{student_id}` | `require("student.view")` + record policy | Fields: the list item's plus `school_name`, `personal`, `guardians` (#34), `family` (#172), `login` (#35), `version`, `created_at`, `updated_at`. |
| `PATCH /api/v1/students/{student_id}` | `require("student.manage")` + record policy | Body `{version, ...}`: any create field and `is_active`; `emergency_contacts` is the complete new list. |

- **Who sees which student** (record policy, pure functions in
  `families/permissions.py`): Owners, Administrators, Academic Coordinators
  and Finance Staff see every student. Tutors see only the students of their
  current assignments; assignments arrive with scheduling (M4), so until
  then tutors see none. Guardians see the students linked to their active
  guardian profile (#34, see Guardians and links) and a student user their
  own profile (the one linked to their login, #35, whatever its
  `is_active`; suspending the login is the access control).
  A student the caller may not see is `404 not_found`, exactly like a
  missing one, and the list leaves it out (never `403`/`404` for hidden
  rows).
- **How much** (owner decision on #33, E2 (a)): `personal` (`birthdate`,
  `email`, `phone`, `emergency_contacts` in position order) is present only
  for full-access viewers: holders of `student.manage`, a linked guardian
  of a **minor** (#34), a linked guardian of an **adult** who gave that
  guardian a `guardian_academic_access` consent (#35) and the student
  themselves (#35). For Finance Staff, assigned tutors and a linked guardian
  of an adult without that consent (ADR-0007 D17; owner decisions on #34,
  E3 (a), and #35, E4 (a)) `personal` is `null`. `is_minor` (under 18 today in the center's
  time zone, ADR-0007 D17) is shown to every viewer.
- **Search** (`q`, 1-100 characters, at most 5 words): ignores case and
  accents (`José` = `jose`, `Ñúñez` = `nunez`) and word order (`garcia jose`
  finds José García); `%`, `_` and `\` match literally. A name also matches
  when it is similar enough to `q` (`pg_trgm` word similarity at its default
  threshold, for example `Gonzales` finds González). With `q`, results are
  ordered by similarity, then by family names and given names (accents
  ignored); without `q`, by family names, given names. The search text never
  reaches the access log (it has no query string).
- **Fields:** strings are trimmed; for an optional string, `""` is `null`.
  Names 1-100 characters; `birthdate` `YYYY-MM-DD`, from 1900-01-01 up to
  today in the center's time zone (a later date is `422 validation_error` on
  `["body","birthdate"]`); `email`/`phone` with the user-profile patterns
  (see User administration); `school_name` up to 200, `school_grade` up to 50
  characters (free text). Duplicates (same name and birthdate) are allowed.
- **Education level:** only an active level can be picked (`422
  education_level_unavailable`); a level that was deactivated after it was
  assigned stays, is shown with `is_active: false` and may be kept.
- **Emergency contacts, whole-list replace:** a `PATCH` that sends
  `emergency_contacts` sends the complete list (1-5). An item with the `id`
  of one of this student's contacts updates it; an item without `id` adds
  one; a contact left out is removed; the list order is the position (1 is
  called first). An unknown or repeated `id` is `422
  emergency_contact_unknown` and changes nothing.
- **Updates:** fields not sent stay unchanged; `null` clears `email`,
  `phone`, `school_name`, `school_grade` and `education_level_id`, and is
  `422` for any other field. Any change, a contacts-only one included,
  increments the profile's `version` once. Check order: `422` → `404` →
  `409 version_conflict` → `422` (future birthdate) → `409
  guardian_consent_required` (a birthdate that makes a student with an
  active login a minor without a valid consent, #35) → `422`
  (`emergency_contact_unknown`, `education_level_unavailable`). A body that
  changes nothing returns `200` with no new version.
- **No hard delete:** `is_active: false` deactivates and `true` reactivates;
  everything else is kept and the profile stays editable. Deletion and
  anonymization belong to retention (M10-07). No reauthentication.
- **Audit:** automatic only (`students` and `student_emergency_contacts`
  `create`/`update`/`delete` with the changed fields, unmasked; a
  contacts-only change writes only contact events); no named event. `403`,
  `404`, `409`, `422` and a body that changes nothing write no event.

### Guardians and links

Implemented by M2-16 (#34; spec §2, §12, §16) in
`apps/api/app/modules/families/`. A guardian profile records a parent or
guardian, with or without a login (staff can record consent on paper for a
guardian without one, ADR-0007 D18).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/guardians` | `require("guardian.view")`, list filtered by record policy | Query `q` (the Students name search), `status` (`active` (default), `inactive` or `all`), offset pagination. Items: `id`, `given_names`, `family_names`, `is_active`, `has_account`, `student_count`; no contact data. |
| `POST /api/v1/guardians` | `require("family.manage")` | Body `{given_names, family_names, email?, phone?, user_id?}`. `201` with the detail. |
| `GET /api/v1/guardians/{guardian_id}` | `require("guardian.view")` + record policy | Fields: the item's (without `student_count`) plus `email`, `phone`, `account`, `students` (`[{student_id, given_names, family_names, relationship, is_minor, is_active}]`), `family` (#172), `version`, `created_at`, `updated_at`. |
| `PATCH /api/v1/guardians/{guardian_id}` | `require("family.manage")` + record policy | Body `{version, ...}`: any create field and `is_active`; `null` clears `email`, `phone` and `user_id`. |
| `POST /api/v1/students/{student_id}/guardians` | `require("family.manage")` + record policy (student) | Body `{guardian_id, relationship}`. `201` with the student's guardian item. |
| `DELETE /api/v1/students/{student_id}/guardians/{guardian_id}` | `require("family.manage")` + record policy (student) | `204`. An unknown student, an unknown guardian and no link are the same `404`. |

- **Profiles:** the profile has its own `email`/`phone` (the user-profile
  patterns); a linked account's contact fields are never copied or shown.
  Names 1-100 characters; duplicates are allowed. No hard delete:
  `is_active: false` deactivates (the links and the account link are kept,
  but the profile grants nothing) and `true` reactivates. Check order of the
  `PATCH`: `422` → `404` → `409 version_conflict` → the account rule → the
  update; a body that changes nothing returns `200` with no new version.
- **One account per profile:** `user_id` links at most one account to one
  profile. The account must exist, not be `archived` and hold the `guardian`
  role (`pending` and `suspended` accounts may be linked); otherwise `422
  guardian_account_unavailable`. An account linked to another profile is
  `409 guardian_account_in_use`. The rule is checked only when `user_id`
  changes, so an already linked account that was archived later stays.
  `account` (`{id, username, display_name, state}`) is shown only to holders
  of `family.manage`; everyone else gets `null` and `has_account`. No
  reauthentication: linking an account is the step that gives a login
  access to children's records, and ADR-0007 D10 doesn't list it.
- **Links:** one link per student and guardian, with a relationship code:
  `mother`, `father`, `grandparent`, `stepparent`, `sibling`,
  `other_relative`, `legal_guardian` or `other`. A student may have several
  guardians and a guardian several students; there is no primary guardian.
  Only an active guardian can be linked (`422 guardian_unavailable`); an
  inactive student can. A duplicate is `409 guardian_already_linked`. To
  change a relationship, unlink and link again. A link never changes either
  profile's `version`. Unlinking hard-deletes the link; its audit `delete`
  event keeps the values.
- **Who sees what:** staff with `guardian.view` (Owners, Administrators,
  Academic Coordinators, Finance Staff) see every profile. A guardian sees
  only their own **active** profile (the list has at most that one row;
  any other profile is `404`, like a missing one), and the students linked
  to it: a **minor** in full, an **adult** with `personal: null` unless the
  student gave this guardian a `guardian_academic_access` consent (owner
  decisions on #34, E3 (a), and #35, E4 (a); see Consent and student
  logins). Tutors
  and student users get `403` on the guardian routes.
- **Access follows the links on the next request:** the guardian's facts are
  loaded on every request, never cached. Unlinking, deactivating the
  profile, moving its account to another profile or removing the
  `guardian` role takes effect on the guardian's next request, without a
  new login.
- **Co-guardians:** `StudentDetail.guardians` (`[{guardian_id, given_names,
  family_names, relationship, phone, is_active, has_account}]`, by family
  names then given names) is a list only for viewers with a staff role and
  `guardian.view`; it is `null` for tutors, student users and linked
  guardians, so a guardian never sees the child's other guardians.
- **Consent on unlinking and deactivation (#35):** unlinking a guardian
  revokes their active consent records for that student (cause
  `guardian_unlinked`); deactivating a profile keeps its records but they
  stop counting. Either may suspend a minor's login (see Consent and
  student logins).
- **Audit:** automatic only (`guardians` and `student_guardians`
  `create`/`update`/`delete`, unmasked; an account link is an `update` of
  `user_id`); no named event and no `details`. `403`, `404`, `409`, `422`
  and a body that changes nothing write no event.

### Consent and student logins

Implemented by M2-17 (#35; ADR-0007 D17, D18; spec §2, §9, §16) in
`apps/api/app/modules/families/` (`logins.py`, `consent.py`).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `PUT /api/v1/students/{student_id}/account` | `require("family.manage")` + record policy | Body `{user_id: UUID or null, version}`. `200` with the student detail. `null` unlinks. |
| `GET /api/v1/students/{student_id}/consents` | `require("consent.record")` + consent record policy | `200` with `{student_id, is_minor, login_state, current_notice_version, guardians, consents, can_record}`. |
| `POST /api/v1/students/{student_id}/consents` | `require("consent.record")` + consent record policy | Body `{scope, guardian_id, method}`. `201` with the record. |
| `POST /api/v1/students/{student_id}/consents/{consent_id}/revoke` | `require("consent.record")` + consent record policy | No body. `200` with `{consent, student_login_suspended}`. |

- **Student logins:** a student profile is linked to at most one login
  account (`students.user_id`, unique) and an account to at most one
  profile. The account sees only that profile, in full, while it holds the
  `student` role. Linking (`PUT …/account`): `422` → `404` → `409
  version_conflict` → `422 student_account_unavailable` (unknown or
  archived) → `409 student_account_in_use` → the gate for this student when
  the account is `active` and holds `student` → the update (a `students`
  `update` of `user_id`). Any non-archived account may be linked, even
  without the `student` role (linking alone grants nothing); unlinking is
  always allowed. `StudentDetail.login` (`{user_id, username, display_name,
  state}`) is shown only to holders of `family.manage`, `null` for everyone
  else.
- **The gate** (ADR-0007 D18, #29 E3): an account holding `student` becomes
  or stays `active` only when a student profile is linked to it (else `409
  student_profile_required`) and that student is an adult today in the
  center's time zone, or a minor with at least one **valid** `student_login`
  consent: not revoked, from a guardian still linked to the student and
  whose profile is active (owner decision on #35, E3 (a)); else `409
  guardian_consent_required`. There is no minimum age. It runs inside
  `identity.service.change_user_state(→ active)` and
  `add_user_role("student")` on an active account, so invitation
  redemption (the link stays usable), registration approval (the request
  stays pending), reactivation and role changes all pass it, and a refusal
  writes nothing. `identity` can't import `families` (ADR-0002), so
  `identity` defines a `StudentLoginGate` hook that the API, the CLI and
  the worker register; with none registered, activating a student fails
  closed (`500`).
- **Consent records:** each records the student, the guardian profile, the
  scope, the method (`in_app`, `paper` or `in_person`), the privacy-notice
  version current when it was given (#174; a later version doesn't
  invalidate it), `granted_at` and who recorded it. Revocation sets
  `revoked_at`, `revoked_by_user_id` and `revocation_cause` (`revoked` or
  `guardian_unlinked`); records are never deleted. One active record per
  student, guardian and scope (`409 consent_already_given`).
  - `student_login` (minors only): the named guardian allows the student's
    login.
  - `guardian_academic_access` (adults only, **per guardian**, owner decision
    on #35, E4 (a)): the adult student shares their academic records with
    the named guardian, who then gets `full` access to the profile. A
    guardian linked later gets nothing until the student consents for them.
  - Who records what (D1): Owners and Administrators record either scope for
    any linked, active guardian with method `paper` or `in_person`, see and
    revoke every record. A guardian records `student_login` for their linked
    minors as their own active profile (`in_app`), sees and revokes only the
    records naming their own profile. A student records
    `guardian_academic_access` for themselves (`in_app`) naming one of their
    linked, active guardians, sees every record of their own student and
    revokes its `guardian_academic_access` records. A student or a record
    the caller can't reach is `404` (like a missing one); a visible record
    they may not revoke is `403`. Check order of the `POST`: `422` → `404` →
    `403` (scope) → `422 consent_method_invalid` → `403` (a guardian naming
    another guardian) / `422 consent_guardian_invalid` → `422
    consent_not_applicable` → `409 consent_already_given`.
  - The overview lists the guardians the caller may name (staff and the
    student: every linked guardian; a guardian: themselves only, never the
    co-guardians), the visible records (active first, then the newest) with
    `can_revoke` and `suspends_login_on_revoke`, and `can_record` (the scopes
    the caller may record now, by role and age).
- **Losing the basis suspends the login** (D18, owner decision on #35, E3
  (a)): after a revocation, an unlink (which revokes the guardian's records
  for that student) or a guardian profile deactivation, a minor's `active`
  student login with no valid consent left is suspended in the same
  transaction through `change_user_state(active → suspended)`: its sessions
  end and `families.student_login_suspended` is written. Revoking one of two
  valid consents keeps the login. Reactivating a guardian profile doesn't
  reactivate the student; staff reactivate it, which passes the gate again.
  An adult's login is never suspended this way. Lock order: the
  last-active-Owner advisory lock (only when the login holds `owner`), the
  user row, then the student row.
- **`guardian_has_academic_access()`** (`families/permissions.py`) is the fact
  later academic policies (M4-M5) must use for guardian reads: a linked
  guardian of a minor, or of an adult with that guardian's
  `guardian_academic_access` consent. Facts are loaded on every request,
  never cached, so a revocation applies on the guardian's next request.
  Billing visibility follows the family (#172), never consent.
- **No reauthentication** for consent or account links (ADR-0007 D10 lists
  staff suspension, not consent): a guardian's withdrawal can suspend a
  student's login without a password prompt.
- **Audit:** `consent_records` is audited with nothing excluded (a `create`
  per record, an `update` per revocation). The named event
  `families.student_login_suspended` has `record_type="users"`, the
  student's account as `record_id` and `details` `{student_id, consent_id
  (or null), cause}` (`consent_revoked`, `guardian_unlinked` or
  `guardian_deactivated`). `403`, `404`, `409`, `422` and gate refusals
  write nothing.

### Families

Implemented by M2-16b (#172; spec §2, §6/W4, §16) in
`apps/api/app/modules/families/`. A family groups students and guardian
profiles; M6-05 attaches the family billing account to it (one per family).
It has no money, ledger or billing account yet.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/families` | `require("guardian.view")`, list filtered by record policy | Query `q` (the Students name search on `name`), `status` (`active` (default), `inactive` or `all`), offset pagination. Items: `id`, `name`, `is_active`, `student_count`; no guardian count. |
| `POST /api/v1/families` | `require("family.manage")` | Body `{name}`. `201` with the detail (`version` 1, no members). |
| `GET /api/v1/families/{family_id}` | `require("guardian.view")` + record policy | Fields: `id`, `name`, `is_active`, `students` (`[{student_id, given_names, family_names, is_minor, is_active}]`), `guardians` (`[{guardian_id, given_names, family_names, phone, has_account, is_active}]`), `version`, `created_at`, `updated_at`; both lists by family names then given names. |
| `PATCH /api/v1/families/{family_id}` | `require("family.manage")` + record policy | Body `{version, name?, is_active?}`; `null` is `422`. |
| `PUT /api/v1/families/{family_id}/students/{student_id}` | `require("family.manage")` | `204`, idempotent. |
| `DELETE /api/v1/families/{family_id}/students/{student_id}` | `require("family.manage")` | `204`. |
| `PUT /api/v1/families/{family_id}/guardians/{guardian_id}` | `require("family.manage")` | `204`, idempotent. |
| `DELETE /api/v1/families/{family_id}/guardians/{guardian_id}` | `require("family.manage")` | `204`. |

- **One family each** (owner decision on #172, E2 (a)): a student belongs to
  at most one family, and so does a guardian profile; the database enforces
  it with unique constraints. A family has any number of students and
  guardians. Names are trimmed, 1-200 characters, and not unique.
- **Membership is not access.** What a guardian sees of a student still
  follows the student-guardian links only (see Guardians and links). A
  guardian can be linked to a child in another family (for example a parent
  who doesn't pay), and a family member who isn't linked to a child doesn't
  see that child's record (`404`). Billing (M6) follows the family; academic
  visibility follows the link and consent (ADR-0007 D17).
- **Adding a member (`PUT`) is idempotent:** a member already in this family
  is `204` and writes nothing. Check order: an unknown family or member (the
  same `404`) → an inactive family (`409 family_inactive`) → the member is in
  **another** family (`409 family_member_conflict`; a concurrent add of one
  member to two families gives one `204` and one `409`) → the insert.
  Inactive students and guardian profiles may be added.
- **Moving is remove, then add:** never one step, so a move can't happen by
  accident (M6 may later refuse a removal while a family has open charges).
  `DELETE` of someone who isn't a member of **this** family, or of unknown
  ids, is `404`. Membership never changes the family's, the student's or the
  guardian's `version`, so adding members while someone renames the family
  causes no `409`.
- **No hard delete:** `is_active: false` deactivates and `true` reactivates;
  the members are kept. An inactive family takes no new members and stays
  visible to its own guardians (with `status=inactive` or `all` in the
  list). Check order of the `PATCH`: `422` → `404` → `409
  version_conflict` → the update; a body that changes nothing returns `200`
  with no new version. No reauthentication.
- **Who sees what:** staff with `guardian.view` (Owners, Administrators,
  Academic Coordinators, Finance Staff) see every family and every guardian
  in it. A guardian sees only the family of their **active** profile: its
  name and its students' names, and only their own profile in `guardians`
  (never the co-guardians). Any other family is `404`, like a missing one,
  and the list has at most that one row. Removing the guardian from the
  family or deactivating their profile applies on their next request.
  Tutors and student users get `403`.
- **`family` on the details:** `StudentDetail.family` and
  `GuardianDetail.family` are `{id, name}` or `null`: filled for viewers with
  a staff role and `guardian.view`, and on `GuardianDetail` also for the
  guardian viewing their own profile; `null` otherwise (and when the profile
  belongs to no family).
- **Audit:** automatic only (`families` `create`/`update`;
  `family_students` and `family_guardians` `create`/`delete` with their
  ids); no named event and no `details`. An idempotent re-add, `403`, `404`,
  `409`, `422` and a body that changes nothing write no event.

### Tutor profiles

Implemented by M3-07 (#43; spec §4) in `apps/api/app/modules/staff/`. A
tutor is a user who holds the `tutor` role now; their profile combines the
account's contact fields with a `tutor_profiles` row. Teaching subjects come
from approved qualifications (#44, below), availability with #45 and
documents with M5-08.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/tutors` | `require("tutor.view")`, list filtered by record policy | Query `q` (part of the username or display name, ignoring case; accents not folded; at most 100 characters), `state` (one user state), offset pagination. Sorted by display name, then username. Items: `user_id`, `username`, `display_name`, `state`, `email`, `phone`. |
| `GET /api/v1/tutors/{user_id}` | `require("tutor.view")` + record policy | The list item fields plus `professional_experience`, `version`, `editable_fields` (sorted) and `teaching_subjects` (#44). |
| `PATCH /api/v1/tutors/{user_id}` | `require("tutor.view")` + record and field policy | Body `{version, display_name?, email?, phone?, professional_experience?}` (unknown fields are `422`). |

- **Who sees what** (owner decision on #43, E1 (a)): `tutor.view` is held by
  Owners, Administrators, Academic Coordinators and Tutors. Owners and
  Administrators (`tutor.manage`) and Academic Coordinators see every tutor;
  a tutor sees only themselves (the list has at most their own row). Finance
  Staff, Students and Guardians get `403` on every route. A user who doesn't
  exist, doesn't hold the `tutor` role now, or isn't visible to the caller
  is the same `404 not_found`. Losing the role hides the profile; its row is
  kept.
- **Contact details live on the account:** `display_name`, `email` and
  `phone` are the `users` fields (#31, same formats; `""` or `null` clears
  `email` and `phone`), so a change here shows on the Users page and the
  other way round. Only `professional_experience` (trimmed, at most 4000
  characters; blank clears it) is stored in `tutor_profiles`.
- **Who edits what** (`editable_fields`): with `tutor.manage` every field;
  the tutor themselves `email`, `phone` and `professional_experience`;
  nobody else anything (Academic Coordinators only view). The account fields
  of another user also follow the User administration rule (an Administrator
  doesn't manage an Owner or another Administrator), so for a tutor who is
  also an Owner an Administrator edits only the experience. Sending a field
  outside `editable_fields` is `403 permission_denied` and changes nothing.
- **Lazy row and `version`:** no row is created when the role is granted. A
  tutor without one has an empty experience and `version` 0; the first
  change creates the row at `version` 1. Every later change, account fields
  included, increments the profile's `version`, so it guards the whole
  page. Of two concurrent first edits from `version` 0, one gets `200` and
  the other `409 version_conflict`. A contact change also increments
  `users.version`, so a Users page loaded before it gets `409` on save.
- **Check order of the `PATCH`:** `422` → `404` → `403 permission_denied`
  (a field outside `editable_fields`) → `409 version_conflict` → the update.
  A body that changes nothing returns `200` with no new version and no
  event. No reauthentication. No new error code.
- **Audit:** automatic only: `tutor_profiles` `create`/`update` (with
  `professional_experience`) and `users` `update` for the account fields. A
  change of account fields alone increments the profile's version without a
  `tutor_profiles` event (unless it creates the row). `403`, `404`, `409`,
  `422` and a body that changes nothing write no event.

### Tutor qualifications

Implemented by M3-08 (#44; spec §3 "subject + education-level
qualifications, approved by an administrator", §16) in
`apps/api/app/modules/staff/` (`qualifications.py`,
`qualification_approval.py`; revocation, M3-08b #198, in
`qualification_revocation.py`). A tutor requests to teach a **subject**; a
subject belongs to exactly one education level (#32), so the level follows
from it.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/tutors/{user_id}/qualifications` | `require("tutor.view")` + the tutor profile record policy | Query `status` (`all` by default, or one status). A JSON array of `QualificationItem`, ordered by the level's `sort_order`, the subject name, then the request time. |
| `POST /api/v1/tutors/{user_id}/qualifications` | `require("tutor.view")`; only the tutor themselves | Body `{subject_id}` (unknown fields are `422`). `201` with the item. |
| `POST /api/v1/tutors/{user_id}/qualifications/{qualification_id}/cancel` | `require("tutor.view")`; only the tutor themselves, only while `pending` | No body. `200` with the item. |
| Approve / reject | `POST /api/v1/approval-requests/{id}/approve` and `/reject` | Holders of `qualification.review` (Owners, Administrators; not delegable). See Approval requests. |
| `POST /api/v1/tutors/{user_id}/qualifications/{qualification_id}/revoke` | `require("qualification.review")` (Owners, Administrators) | Body `{reason, version}` (`QualificationRevoke`, unknown fields are `422`). `200` with the item. No reauthentication (not in ADR-0007 D10). |

- **Model** (`tutor_qualifications`, versioned, audited): `tutor_user_id`
  and `subject_id` (both `ON DELETE RESTRICT`), `status` (`pending`,
  `approved`, `rejected`, `cancelled`, `revoked`), `decided_at` (set
  exactly for the four final statuses; a revoked row keeps its approval
  decision), `decided_by_user_id`, `rejection_reason` (set exactly for
  `rejected`, at most 1000 characters), and `revoked_at`,
  `revoked_by_user_id` (`users`, `ON DELETE RESTRICT`) and
  `revocation_reason` (at most 1000 characters), all three set exactly for
  `revoked` (#198, migration 0028). The qualification keeps
  its own status, mirrored with its approval request in one transaction, so
  eligibility never reads the approval tables (ADR-0002).
- **No duplicate active qualification:** the partial unique index
  `uq_tutor_qualifications_active` on `(tutor_user_id, subject_id) WHERE
  status IN ('pending', 'approved')`. A rejected, cancelled or revoked
  qualification doesn't block a new request for the same subject.
- **`QualificationItem`:** `id`, `subject_id`, `subject_name`,
  `education_level_id`, `education_level_name`, `subject_is_active`,
  `status`, `requested_at`, `decided_at`, `rejection_reason`, `revoked_at`,
  `revocation_reason`, `version`. `GET …/qualifications?status=revoked`
  lists the revoked ones.
- **Requesting:** only the tutor themselves (holding `tutor`, active);
  administrators don't request on a tutor's behalf. Order: `422` (body) →
  `404` (unknown tutor, or one the caller can't see) → `403
  permission_denied` (a caller who sees the tutor but isn't them) → `422` on
  `["body", "subject_id"]` (unknown subject; code `validation_error`) → `409
  subject_inactive` (the subject or its level is inactive; both rows are
  locked `FOR SHARE`) → `409 qualification_exists` (a pre-check, plus the
  unique index at flush, so two concurrent requests give one `201` and one
  `409`). The `pending` row and a `staff.qualification` approval request
  (requester: the tutor) are created in one transaction.
- **Cancelling:** `404` (tutor) → `403` → `404` (no such qualification of
  this tutor) → `409 approval_transition_invalid` unless `pending`. Sets
  `cancelled` and `decided_at` and cancels the approval request.
- **The `staff.qualification` approval type:** review permission
  `qualification.review`; no request-changes (`409
  approval_action_unsupported`); a rejection needs a reason (`422` without).
  The summary is `{tutor_display_name, subject_name, education_level_name}`.
  Approving or rejecting locks the qualification, which must still be
  `pending` (else `409 approval_transition_invalid`), sets the status, the
  decision fields and (on reject) the reason, and creates one notification
  for the tutor, all in the decision's transaction: of a concurrent approve
  and reject exactly one wins and exactly one notification exists.
- **Revoking (#198):** a holder of `qualification.review` withdraws an
  **approved** qualification with a reason (trimmed, 1–1000 characters).
  Order: `403 permission_denied` (a Coordinator, Finance, any tutor
  including the tutor themselves, a Student or a Guardian) → `422` (body) →
  `404` (the qualification, locked `FOR UPDATE`, is missing or isn't
  `user_id`'s) → `409 version_conflict` (a stale `version`) → `409
  qualification_not_approved` (pending ones are rejected through the
  approval inbox; rejected, cancelled and revoked ones can't be revoked).
  Then `status = 'revoked'`, `revoked_at`, `revoked_by_user_id` and
  `revocation_reason` are set, the `version` is bumped, and the tutor's
  notification is created in the same transaction: of two concurrent
  revokes with one version, one gets `200`, the other `409
  version_conflict`, and exactly one notification exists. Any approved
  qualification can be revoked, whatever the tutor's current roles or state
  and whatever the subject's or level's active state. Nothing is deleted.
  **The `staff.qualification` approval request is not touched:** it stays
  `approved` as the record of the original decision;
  `tutor_qualifications.status` is the source of truth for eligibility.
  After a revocation the tutor may request the same subject again (a new
  `pending` row; the revoked row stays in the list). Class sessions are not
  read or changed: M4 evaluates qualification live
  (`staff.service.is_qualified`).
- **Notifications:** `staff.qualification_approved`,
  `staff.qualification_rejected` and `staff.qualification_revoked` (#198),
  params `{subject_name, education_level_name}`. The reason is not a param
  (the 200-character param cap): the tutor reads it on their qualifications
  list. The web app links all three to the tutor's own
  `/tutors/<id>#qualifications`.
- **Only approved counts:** `TutorDetail.teaching_subjects` lists the
  approved qualifications (`subject_id`, `subject_name`,
  `education_level_id`, `education_level_name`, `subject_is_active`) by the
  level's order, then the subject name. For other modules
  `staff.service` exports `approved_subject_ids(session, tutor_user_id)`,
  `is_qualified(session, tutor_user_id, subject_id)` and
  `qualified_tutor_ids(session, subject_id)`; pending, rejected, cancelled
  and revoked never count (a revocation takes effect at once). A
  later-deactivated subject keeps its approved qualifications (shown with
  `subject_is_active: false`). Whether the user still holds the `tutor` role
  is the caller's check (M4-06).
- **Audit:** automatic `tutor_qualifications` `create` (actor: the tutor)
  and `update` events (actor: the tutor for a cancel, the reviewer for a
  decision or a revocation, with `status`, `revoked_at`,
  `revoked_by_user_id` and `revocation_reason`), plus the approval request's
  own events.

### Tutor availability

Implemented by M3-09 (#45; spec §3, §16; ADR-0004) in
`apps/api/app/modules/staff/` (`availability.py`: the pure rules;
`tutor_availability.py`: the routes' service; `assignment_impact.py`: the
M4-18 seam). Teaching limits, the required break and preferred classrooms
were added by M3-09b (#197, same files).

**Local wall-clock rule:** every availability value is a local `DATE` or
`TIME` of the center (`business_settings.time_zone`), never an instant. A
weekly window 16:00–20:00 means 16:00–20:00 local time on that weekday,
before and after a DST change. So availability is not "scheduled data" for
the time-zone change guard (#37).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/tutors/{user_id}/availability` | `require("tutor.view")` + the tutor profile record policy | `TutorAvailabilityDetail`: `weekly` (windows by weekday, then start; `HH:MM`), `exceptions` (by `starts_on`, whole days first, then `starts_at`), `limits` (`{daily_limit_minutes, weekly_limit_minutes, required_break_minutes}`, `null` = none), `preferred_classrooms` (`[{id, name, is_active}]` by name), `version` (`0` while nothing was ever changed). |
| `PUT /api/v1/tutors/{user_id}/availability/weekly` | `require("tutor.view")`; the tutor themselves or `tutor.manage` | Body `{version, windows: [{weekday, starts_at, ends_at}]}`, at most 42 windows, the classroom window checks (`window_overlap` on the later item's `starts_at`). Replaces the windows; `200` with the detail. |
| `PATCH /api/v1/tutors/{user_id}/availability/settings` | as above | Body `{version, daily_limit_minutes?, weekly_limit_minutes?, required_break_minutes?, preferred_classroom_ids?}`; fields not sent stay, a limit sent as `null` is cleared, `preferred_classroom_ids` replaces the list. `200` with the detail. No impact check. |
| `POST /api/v1/tutors/{user_id}/availability/exceptions` | as above | Body `{kind, starts_on, ends_on, starts_at?, ends_at?, note?}`. `201` with the `AvailabilityExceptionItem`. |
| `DELETE /api/v1/tutors/{user_id}/availability/exceptions/{exception_id}` | as above | `204`. An id of another tutor's exception is `404`. |
| `GET /api/v1/tutors/{user_id}/availability/check` | `require("tutor.view")` + the record policy | Query `date` (`YYYY-MM-DD`), `start`, `end` (`HH:MM`, `start < end`; else `422`). `{available, reasons}`. |

- **Edit rights:** the tutor themselves and holders of `tutor.manage`
  (Owners, Administrators). An Academic Coordinator reads and checks but
  gets `403 permission_denied` on writes; a caller who can't see the tutor
  gets `404` everywhere (as a missing tutor or a user without the `tutor`
  role).
- **Weekly windows** (`tutor_availability_windows`): ISO weekday,
  `[starts_at, ends_at)`, whole minutes, never across midnight; windows of
  one weekday may touch but never overlap (`ex_tutor_windows_no_overlap`).
  The `PUT` order is `422` → `404` → `403` → `409 version_conflict` → the
  impact check → the update. Only the windows that changed are deleted and
  inserted. The `version` lives on `tutor_availability_settings`, created by
  the first change (from `version` 0); a concurrent first change that loses
  is `409 version_conflict`. A body that changes nothing keeps the version.
- **Exceptions** (`tutor_availability_exceptions`): `kind` `unavailable`
  (time off: the whole day when both times are omitted, otherwise those
  hours of each day) or `available` (extra hours of each day; times are
  required). Inclusive dates at most 366 days apart; times both or neither,
  `starts_at < ends_at`; `note` up to 200 characters (trimmed; blank is
  none). Exceptions may overlap. They are created and deleted, never
  edited, and don't change the weekly `version`.
- **Precedence and the check** (`availability.evaluate`), every applicable
  reason in this order:
  1. `center_closed`: an active closure contains the date (#38);
  2. `time_off`: an `unavailable` exception on that date is whole-day, or
     its hours overlap `[start, end)` (touching doesn't overlap);
     `unavailable` wins over `available`;
  3. `outside_weekly_availability`: `[start, end)` is not covered by the
     union of that weekday's windows and the date's `available` hours
     (touching ranges join). A tutor without windows is never available.
- **For M4:** `staff.service.is_tutor_available(session, tutor_user_id,
  local_date, start, end)` and `is_tutor_available_at(session,
  tutor_user_id, starts_at, ends_at)` return `AvailabilityResult(available,
  reasons)`. The second converts the instants with the center zone into
  local pieces (`availability.to_local_intervals`): a piece per local day
  (a piece to midnight ends at "24:00", which no window covers); a DST gap
  stays one piece (01:30–03:30 wall time for one real hour); the repeated
  hour of a DST overlap gives two pieces, both checked. It is available only
  if every piece is; reasons are merged. Neither checks the tutor role.
- **Impact seam (M4-18 #63):** the weekly `PUT` (only when it takes weekly
  time away: `removed_windows` is what the old windows covered and the new
  ones don't), an `unavailable` exception `POST` and an `available`
  exception `DELETE` (both as `added_unavailability`) ask
  `AvailabilityImpactSource.affected_session_ids`. A non-empty answer is
  `409 availability_change_requires_approval` and nothing changes. Until M4
  the app wires `NoAssignmentImpact` (always empty, no SQL).
- **Teaching limits** (#197; spec §3) on `tutor_availability_settings`,
  minutes, each optional (`null`: no limit, no break):
  `daily_limit_minutes` 15–1440, `weekly_limit_minutes` 15–10080 and not
  below the daily limit, `required_break_minutes` 0–240 (`0` is a value,
  "no break" is `null`). The database has the same CHECKs. **Warnings
  only:** M4-05 (#50) compares a tutor's workload with them and warns; they
  never block an assignment (ISSUE-D4), so changing them asks no impact
  check.
- **Preferred classrooms** (#197; `tutor_preferred_classrooms`, unique per
  tutor and room): at most 10, each once (`duplicate_classroom`). They
  guide scheduling suggestions (M4-05, M4-06) and never restrict them.
  Names come from `catalog.service.classroom_references`. A newly chosen
  room must exist and be active (`catalog.service.active_classroom_ids`);
  otherwise `422 validation_error` on `["body","preferred_classroom_ids"]`,
  code `classroom_unavailable`. A stored room deactivated later stays (with
  `is_active: false`) and may be kept when the list is edited, until it is
  removed.
- **Settings `PATCH` order:** `422` (bounds, a weekly limit below the daily
  one when both are sent, more than 10 rooms, duplicates, `null` rooms,
  unknown fields) → `404` → `403` → `409 version_conflict` → `422`
  `weekly_below_daily` on `["body","weekly_limit_minutes"]` when the weekly
  limit would fall below the daily one combined with the stored limits →
  `422 classroom_unavailable` → the update. The `version` is the same as
  the weekly windows' (one settings row): a weekly `PUT` and a settings
  `PATCH` each bump it. A body that changes nothing keeps it.
- **For M4:** `staff.service.teaching_limits(session, tutor_user_id)`
  returns `TeachingLimits(daily_limit_minutes, weekly_limit_minutes,
  required_break_minutes)` (all `None` when never set);
  `staff.service.preferred_classroom_ids(session, tutor_user_id)` returns
  the stored room ids in the order chosen, inactive ones included (filter
  with `catalog.service.active_classroom_ids`).
- **Audit:** all four tables are audited: `create`/`delete` per changed
  window, `create`/`delete` per exception, `create`/`delete` per preferred
  room, and the settings row's `create` on the first change and `update`
  with the changed limits (a change of only windows or rooms bumps its
  `version`, which is never audited, #22).

### Course templates

Implemented by M4-19 (#64; spec §3 structured course; owner decisions E1
(a), E2 (a), E3 (a)) in the new `apps/api/app/modules/courses/` module
(`models.py`, `schemas.py`, `templates.py`, `service.py`, `router.py`,
`errors.py`). A course template is reusable syllabus content: a name,
subjects, a duration, ordered modules, assessment definitions and
completion criteria. Templates are created, edited and deactivated, never
deleted. Cohorts (#65) are created from an immutable **snapshot** of a
template, so later edits never change existing cohorts.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/course-templates` | `require("course.view")` | Query `status` (`active` (default), `inactive`, `all`), offset pagination. Sorted by name (not case-sensitive), then `id`. Items (`CourseTemplateSummary`): `id`, `name`, `subjects` (`id`, `name`, `level_name`, `is_active`), `session_count`, `session_minutes`, `total_minutes`, `module_count`, `is_active`, `version`, `updated_at`. At most three statements per page. |
| `POST /api/v1/course-templates` | `require("course.manage")` | Body `CourseTemplateWrite` (below), without item ids. `201` with the detail. |
| `GET /api/v1/course-templates/{template_id}` | `require("course.view")` | Active or inactive. `CourseTemplateDetail`: the summary fields plus `description`, `min_attendance_percent`, `modules`, `assessments`, `completion` and `created_at`. |
| `PUT /api/v1/course-templates/{template_id}` | `require("course.manage")` | Body `CourseTemplateWrite` plus `version` (required): replaces the whole content. `200` with the detail. |
| `PATCH /api/v1/course-templates/{template_id}` | `require("course.manage")` | Body exactly `{version, is_active}`: deactivate or reactivate. |

`course.view` is held by Owners, Administrators, Academic Coordinators and
Finance Staff; `course.manage` by Owners, Administrators and Academic
Coordinators. Neither is delegable. Tutors, students and guardians will see
a cohort's syllabus through the cohort (#65, #67). No reauthentication.

- **Document** (`CourseTemplateWrite`, unknown fields are `422`): `name`
  (trimmed, 1 to 100 characters, unique ignoring case, inactive templates
  included: `409 name_taken`), `description?` (at most 2000 characters;
  blank is `null`), `subject_ids` (1 to 10, no repeats), `session_count`
  (1 to 200, ADR-0005 D4) and `session_minutes` (15 to 480; `total_minutes`
  is their product), `min_attendance_percent?` (1 to 100, `null` = no
  attendance criterion), `modules` (1 to 50: there are no drafts) and
  `assessments` (0 to 30). **List order is position** (1-based in the
  responses).
- **Module:** `{id?, title, description?, subject_id?, planned_sessions?,
  is_required, materials?}`. Titles (1 to 200 characters) are unique within
  the template ignoring case. `subject_id`, when set, must be one of the
  template's `subject_ids`. The set `planned_sessions` (1 to 200 each) add
  up to at most `session_count`. `materials` is free-text materials
  references (at most 2000 characters; no files or links: M5-08 adds library
  resources).
- **Assessment definition:** `{id?, title, module_index?, is_required,
  min_score_percent?}`. `module_index` is the **0-based index of a module in
  the same body** (`null` = the whole course), so new modules and the
  assessments that point at them are saved together. Titles are unique
  within the template ignoring case. Scores are recorded later (M5-13 links
  evaluations to the definition ids of a cohort's snapshot).
- **Completion criteria** (the detail's and the snapshot's `completion`):
  `min_attendance_percent`, `required_module_ids` and
  `required_assessment_ids` (the items with `is_required`, in position
  order). A template without any criterion is valid. M5-18 evaluates them.
- **Replacing (`PUT`):** modules and assessments with an `id` of this
  template are kept and updated; items without `id` are created; stored items
  left out are **deleted** (cohorts keep them in their snapshot). An `id` of
  another template, an unknown one, or one repeated in a list is `422` on
  `["body","modules",i,"id"]` (code `item_unknown`); any `id` in a `POST`
  too. Only changed rows are written. Any change bumps the template
  `version` exactly once (the template row is the version anchor of its
  subjects, modules and assessments); a document that changes nothing returns
  `200` with no new version. Removing a module while repointing its
  assessment (to another module or the whole course) in the same body
  succeeds.
- **Subjects:** a subject the document adds must exist (`subject_unknown`)
  and be active with an active level (`subject_inactive`), as `422` field
  errors on `["body","subject_ids",i]` (several can fail at once). A subject
  linked earlier and deactivated since may stay; it is shown with
  `is_active: false`.
- **Rule errors** are `422 validation_error` field errors with item
  indexes: `too_short` / `too_long` (counts), `duplicate_subject`,
  `duplicate_title` (`[…,i,"title"]`), `module_subject_not_in_template`
  (`["body","modules",i,"subject_id"]`), `planned_sessions_exceed_total`
  (`["body","modules"]`), `module_index_invalid`
  (`["body","assessments",i,"module_index"]`), plus `subject_unknown`,
  `subject_inactive` and `item_unknown`. All are reported together.
- **Check order** of a write: `422` (the body's schema) → `404` → `409
  version_conflict` → `422` (the rules above) → `409 name_taken`. The
  template row is locked `FOR UPDATE`, so concurrent `PUT`s from one version
  give one `200` and one `409 version_conflict`.
- **Snapshots** (for #65): `courses.service.snapshot_course_template(session,
  template_id)` locks the template `FOR SHARE` (a concurrent `PUT` waits until
  the caller's transaction ends), raises the plain exceptions
  `CourseTemplateNotFound` / `CourseTemplateInactive` (#65 maps them), and
  returns a frozen `CourseTemplateSnapshot` (`id`, `template_id`,
  `template_version`, `content`): the stored snapshot of the template's
  current version, or a new one (one row per template and version; a
  concurrent snapshot of the same version gives the same row). It flushes in
  the caller's transaction and never commits.
  `get_course_template_snapshot(session, snapshot_id)` reads one back
  (`None` when unknown). `course_template_snapshots` rows are never updated
  or deleted (trigger `course_template_snapshots_immutable`). No route
  exposes snapshots yet.
- **Snapshot content, schema version 1** (a contract: a change needs a new
  `schema_version`, never an edit of stored rows): `name`, `description`,
  `session_count`, `session_minutes`, `subjects` (`id`, `name`, `level_id`,
  `level_name` at snapshot time), `modules` (`id`, `position`, `title`,
  `description`, `subject_id`, `planned_sessions`, `is_required`,
  `materials`), `assessments` (`id`, `position`, `title`, `module_id`,
  `is_required`, `min_score_percent`) and `completion`. Ids are strings;
  lists are in position order (subjects in catalog order). The same template
  always gives the same content.
- **No time-zone dependency:** templates store no instants (#37).
- **No reference or demo templates.**
- **Audit:** automatic only: `create`/`update`/`delete` of
  `course_templates`, `course_template_subjects`, `course_template_modules`
  and `course_template_assessments` with the changed fields, and one
  `course_template_snapshots` `create` (its `content` is left out; the row is
  the record). A change of the children only bumps the template's `version`
  without a `course_templates` event (`version` and `updated_at` are never
  audited, #22). `403`, `404`, `409`, `422` and a document that changes
  nothing write no event.

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
   `PATCH /api/v1/users/{user_id}` and the state routes) and M2-14
   (`education_levels`, `subjects`: their `PATCH` routes). The
   `version_conflict` error exists since M1-09. Exception:
   `user_preferences` (#36) has no `version`; only the user changes their own
   preferences, the two fields are independent and the last write wins.

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
  - `apiCall` handles four answers for every caller: a `401`
    `authentication_required` or `session_expired` clears the token and the
    whole query cache once and sends the user to `/login?next=<page>` (with
    the "session expired" notice only for `session_expired`); `403
    csrf_failed` refetches `/auth/me` and retries the request once (never if
    the user changed: then the cache is cleared and the app goes to `/`);
    `403 reauth_required` opens the password dialog and retries the request
    once after a confirmed password (cancel rejects with the original error).
    A second `csrf_failed` or `reauth_required` reaches the caller. `403
    privacy_notice_required` refetches `/auth/me` once (concurrent failures
    share it; nothing is retried), and the guard then shows the privacy
    notice (#174).
  - Login and reauthentication are direct calls, not mutations, so the
    password never enters the TanStack mutation cache. Login and logout clear
    every cached response.
  - Route guards and hidden navigation are UX only; the API checks every
    request (spec §16).
- The local/DEV demo job route (`/api/v1/dev/demo-jobs`) is served only when
  `APP_ENV` is `local` or `dev` and is not part of the exported document.
