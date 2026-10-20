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
| `registration_already_decided` | 409 | Approving or rejecting a registration request that was already approved or rejected, or whose account is no longer `pending` (see Registration requests). |
| `user_not_active` | 409 | `POST /api/v1/password-resets` for a user who is not `active` (pending, suspended or archived). |
| `last_active_owner` | 409 | Suspending or archiving the only active Owner, or taking the `owner` role from them (see User administration). |
| `name_taken` | 409 | A catalog record (an education level, or a subject within its level) would get a name another record already has, ignoring case; inactive records count (see Academic catalog). |
| `education_level_has_active_subjects` | 409 | Deactivating an education level that still has an active subject (see Academic catalog). |
| `education_level_inactive` | 409 | Creating or reactivating a subject of an inactive education level (see Academic catalog). |
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
  tz_change_requires_migration` and changes nothing. M3-01 registers no
  check: M4-02 (#47, class sessions) and M4-08 (#53, recurrence series)
  register theirs, and read the zone with
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
