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
| `invalid_credentials` | 401 | `POST /api/v1/auth/login` did not succeed, for any reason (the same body whether or not the account exists). |
| `permission_denied` | 403 | The user lacks the route's permission, or may see the record but not take the action. |
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
  their `401`/`403`. The permission matrix (`_docs/testing-guidelines.md`)
  fails CI for a route without a declaration.
- **The current user** comes only from `get_current_principal`. A `Principal`
  holds the user id, role codes and effective permissions, never a secret.
  Services, workflows and jobs check a permission outside a route with
  `ensure_permission(principal, permission)`.
- **Check order.** Without a signed-in user the answer is `401
  authentication_required`; without the permission it is `403
  permission_denied`. Both come before path, query and body validation, so an
  unauthorized caller never sees a `422`. Exception: a body that is not
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

Implemented by M2-05 (ADR-0007 D7, D8) in `apps/api/app/modules/identity/`.

- **Endpoints:**
  - `POST /api/v1/auth/login` (public): body `{"username", "password"}`.
    `200` sets the session cookie and returns the signed-in user
    (`id`, `username`, `display_name`, sorted `roles` and `permissions`). Any
    failure is `401 invalid_credentials` with one fixed body, whether or not
    the account exists.
  - `POST /api/v1/auth/logout` (`authenticated()`): revokes the current session
    only, clears the cookie, `204`.
  - `GET /api/v1/auth/me` (`authenticated()`): the same body as login.
- **Cookie:** `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`, and no
  `Max-Age` yet (a browser-session cookie). With `SESSION_COOKIE_SECURE=true`
  (the default, every deployed environment) it is `__Host-classora_session`
  with `Secure`; with `false` (allowed only when `APP_ENV=local`, for plain-HTTP
  local runs) it is `classora_session` without `Secure`. The API reads only
  the current mode's name.
- **Token:** 32 random bytes in unpadded base64url. The server stores only its
  SHA-256 hash, so the token is never stored, logged, audited or returned in a
  body.
- **Rotation:** every login issues a new session; a live session on the
  request's cookie is revoked, and a token supplied by the client is never
  adopted.

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
   integer `version`; update requests send it back; a mismatch returns
   `409 version_conflict`. PROPOSED (decided by architecture ISSUE-D5,
   implemented by the first editable-record task). The `version_conflict` error
   already exists (M1-09).

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
- The local/DEV demo job route (`/api/v1/dev/demo-jobs`) is served only when
  `APP_ENV` is `local` or `dev` and is not part of the exported document.
