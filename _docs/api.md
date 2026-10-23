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
| `conflicts`  | no     | Scheduling conflicts (`409 scheduling_conflict`, ADR-0005): a list of `{code, severity, details}`, `severity` `hard` or `soft` (see Class sessions). |
| `details`    | no     | A flat object of short codes for a few state errors (M4-07): `class_session_not_offerable` `{state}` or `{reason: "started"}`, `offer_not_pending` `{status}`, `series_not_offerable` `{reason}` (M4-07b), `class_session_not_substitutable` `{reason, state?, substitution_id?}` and `class_session_not_cancellable` `{reason, state?}` (M4-10), `already_enrolled` `{enrollment_id}` (M4-11). Never record data. |

- A field error's `path` is the location as a list of strings and integers
  (for example `["body", "items", 0, "amount"]`), `code` is the Pydantic error
  type (for example `missing`) and `message` is its text. The submitted value is
  never included.
- No response contains internal details (exception class or message, traceback,
  SQL, DSN, host names) in any environment. Unexpected exceptions become
  `500 internal_error` and are logged with their traceback.
- The OpenAPI document contains one `ProblemDetails` schema (and `FieldError`
  for the `errors` items, `SchedulingConflict` for the `conflicts` items). Every operation that can return `422` documents it as
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
| `rate_limited` | 429 | Too many attempts on a rate-limited endpoint (login, reauthentication and the password change, one-time link lookup and redemption, self-registration, available slots and session requests); a `Retry-After` header gives the seconds to wait. The same body whether or not the account exists (see Rate limits). |
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
| `availability_change_stale` | 409 | Approving a `staff.availability_change` inbox item whose weekly change was made from an older availability `version`, or whose exception to delete is already gone; the item stays pending (see Tutor availability, "Changes that affect scheduled classes"). |
| `availability_change_not_pending` | 409 | `POST /api/v1/tutors/{user_id}/availability/change-requests/{change_request_id}/cancel` for a request that was already approved, rejected or withdrawn; `details.status` is its status (see Tutor availability). |
| `scheduling_conflict` | 409 | A class session that breaks hard or soft scheduling rules (ADR-0005); `conflicts` lists every one and nothing is created. A holder of `schedule.override` can resend with an `override` of the soft ones (M4-04). For a series (M4-08) the list covers every occurrence, each with `details.occurrence_start` and `details.date`; a series `override` lists (occurrence, code) pairs (M4-09c). See Class sessions and Class series. |
| `class_session_not_assignable` | 409 | `GET /api/v1/class-sessions/{class_session_id}/replacement-tutors` for a session that is `completed`, `cancelled` or `skipped_closure`: it can no longer take a tutor (see Class sessions, "Tutor suggestions"). |
| `class_session_not_offerable` | 409 | Offering a session that is not `unassigned` (`details.state`), or one that has begun (`details.reason = "started"`; also a create with a tutor whose start is not after now). See Assignment offers. |
| `offer_not_pending` | 409 | Accepting or declining an offer that was already answered, expired or withdrawn; `details.status` is its status (see Assignment offers). |
| `offer_expired` | 409 | Accepting or declining an offer at or after its `respond_by`; the expiry task closes it within five minutes (see Assignment offers). |
| `reassignment_still_needed` | 409 | "Mark resolved" (approve) on a `scheduling.offer_reassignment` inbox item while its session is still `unassigned` (for a series offer, also while the series has no tutor); the item stays pending (see Assignment offers). |
| `series_not_offerable` | 409 | `POST /api/v1/class-series/{class_series_id}/offers` for a series that already has a tutor (`details.reason = "assigned"`) or with no occurrence still to come from `from_date` (`"ended"`); also a series created with a tutor that has no occurrence still to come (see Assignment offers, "Series offers"). |
| `session_not_editable` | 409 | `PATCH /api/v1/class-sessions/{class_session_id}` on a session that has begun (`details.reason = "started"`) or is `completed`, `cancelled` or `skipped_closure` (`details.state`); see Class sessions, "Moving a session". |
| `group_not_found` | 404 | A tutoring group that doesn't exist or that the caller can't see (M4-11; never `403`); see Tutoring groups. |
| `enrollment_not_found` | 404 | Withdrawing an enrollment that doesn't exist or belongs to another group (see Tutoring groups). |
| `group_name_taken` | 409 | Creating or renaming a tutoring group to a name another group has, ignoring case (see Tutoring groups). |
| `group_ended` | 409 | Enrolling in a group that has no series still producing sessions (see Tutoring groups). |
| `already_enrolled` | 409 | Enrolling a student who already has an active enrollment in the group (`details.enrollment_id`; see Tutoring groups). |
| `enrollment_not_active` | 409 | Withdrawing an enrollment that was already withdrawn (see Tutoring groups). |
| `cohort_not_found` | 404 | A course cohort that doesn't exist (M4-20; see Course cohorts). |
| `cohort_name_taken` | 409 | Creating or renaming a cohort to a name another cohort has, ignoring case (see Course cohorts). |
| `template_unknown` / `template_inactive` | 422 | `POST /api/v1/cohorts` with an unknown or an inactive course template; on `["body","template_id"]` (see Course cohorts). |
| `subject_not_in_template` | 422 | `POST /api/v1/cohorts` with a subject that isn't one of the template snapshot's subjects; on `["body","subject_id"]`. |
| `too_many_occurrences` | 422 | `POST /api/v1/cohorts` whose weekly rule can't fit the template's sessions in 200 occurrences around the known closures; on `["body","recurrence"]`. |
| `class_session_not_substitutable` | 409 | `POST /api/v1/class-sessions/{class_session_id}/substitutions` on a session that is not `confirmed` (`details.reason = "state"`, `details.state`), has begun (`started`) or already has a pending substitution (`substitution_pending`, `details.substitution_id`); see Substitutions. |
| `substitution_no_longer_valid` | 409 | The substitute accepts a substitution whose session is no longer `confirmed` with the original tutor at the offered times; nothing is written (see Substitutions). |
| `class_session_not_cancellable` | 409 | `POST /api/v1/class-sessions/{class_session_id}/cancel` on a session that is `completed`, `cancelled` or `skipped_closure` (`details.reason = "state"`, `details.state`) or has begun (`started`); see Class sessions, "Cancelling a session". |
| `session_not_in_conflict` | 409 | `POST /api/v1/class-sessions/{class_session_id}/conflict-overrides` on a session that isn't a `conflict` occurrence (`details.state`); see Class series, "Series overrides". |
| `series_offer_pending` | 409 | A series edit while the series' tutor offer is waiting for an answer: answer or withdraw it first (see Class series, "Editing and cancelling"). |
| `series_ended` | 409 | A series edit or cancellation from a date after the series' `last_occurrence_on`, a repeated cancellation included (see Class series, "Editing and cancelling"). |
| `occurrence_started` | 409 | A series edit or cancellation from a date on which an occurrence of the series has begun (see Class series, "Editing and cancelling"). |
| `from_date_in_past` | 422 | A series offer, edit or cancellation whose `from_date` is before the center's local today; the field error is on `["body","from_date"]` (see Assignment offers, "Series offers", and Class series, "Editing and cancelling"). |
| `from_date_required` / `from_date_not_allowed` | 422 | A series edit or cancellation with `mode = "following"` and no `from_date`, or `mode = "all"` with one; on `["body","from_date"]`. |
| `session_unchanged` / `series_unchanged` | 422 | A session move with the same times and room, or a series edit whose new rule equals the series' (and no edited occurrence is in scope); on `["body"]`. |
| `student_consent_required` | 403 | A linked guardian acting for an **adult** student who hasn't given that guardian the `guardian_academic_access` consent (available slots, session requests; M4-14). See Session requests. |
| `slot_unavailable` | 409 | `POST /api/v1/session-requests` for a time that no longer passes the slot checks. Opaque on purpose: no reason, no `conflicts` (see Session requests). |
| `session_request_overlap` | 409 | `POST /api/v1/session-requests` while the student has a `pending` request at an overlapping time (see Session requests). |
| `session_request_limit_reached` | 409 | `POST /api/v1/session-requests` while the student already has 5 `pending` requests (see Session requests). |
| `session_request_not_pending` | 409 | Withdrawing, confirming or rejecting a session request that was already confirmed, rejected or withdrawn. The staff routes add `details.status`; the family's withdraw body stays closed (see Session requests). |
| `session_request_started` | 409 | `POST /api/v1/session-requests/{id}/confirm` once the requested time has started (`starts_at <= now`); reject it instead (M4-15, see Session requests). |
| `session_request_review_route_required` | 409 | The inbox's generic approve or reject of a `scheduling.session_request` item whose request the review routes haven't decided; `details.review_path` is the staff review page (`/schedule/requests/<id>`). The item stays pending (M4-15, see Session requests). |
| `change_request_kind_unsupported` | 409 | `POST /api/v1/change-requests` for a `group` or `course` session: only individual sessions are changed by request (`details.kind`; M4-17, see Change requests). |
| `class_session_not_changeable` | 409 | `POST /api/v1/change-requests` for a session that isn't `confirmed` (a tutor's `offered` session, a `completed` one, ...; `details.state`). |
| `change_request_session_started` | 409 | Creating or approving a change request once the session has started (`starts_at <= now`); staff reject it instead (see Change requests). |
| `change_request_pending` | 409 | `POST /api/v1/change-requests` while the session already has a pending request; `details.change_request_id` only when the caller may read that request. |
| `change_request_not_pending` | 409 | Withdrawing, approving or rejecting a change request that was already approved, rejected or withdrawn. The staff routes add `details.status`; the requester's withdraw body stays closed. |
| `change_request_session_changed` | 409 | Approving a change request whose session is no longer `confirmed` (staff cancelled it, a closure skipped it, ...; `details.state`); reject it instead. |
| `change_request_review_route_required` | 409 | The inbox's generic approve or reject of a `scheduling.change_request` item whose request the review routes haven't decided; `details.review_path` is `/schedule/change-requests/<id>`. The item stays pending. |
| `proposal_required` / `proposal_not_allowed` | 422 | A reschedule request without `proposed_starts_at`, or a cancellation with one; on `["body","proposed_starts_at"]`. |
| `proposal_in_past` / `proposal_unchanged` | 422 | A reschedule proposal at or before now, or equal to the session's start; on `["body","proposed_starts_at"]`. |
| `exception_not_applicable` | 422 | Approving as an exception a change request that is already `excused` (every tutor request); on `["body","exception"]`. |
| `classroom_not_applicable` / `override_not_applicable` | 422 | `classroom_id` or `override` on the approval of a cancellation request (they only apply to a reschedule). |
| `not_on_grid` | 422 | A session request whose local start isn't on the hour or the half hour (center zone); on `["body","starts_at"]`. |
| `range_in_past` / `range_too_far` | 422 | Available slots from a date before the center's today (`["query","from"]`) or to a date more than 60 days ahead (`["query","to"]`). |
| `respond_by_invalid` | 422 | An offer deadline that is not after now or is later than the default (the policy's response time, never after the start); the field error is on `["body","respond_by"]` (see Assignment offers). |
| `respond_by_requires_tutor` | 422 | `POST /api/v1/class-sessions` or `POST /api/v1/class-series` with `respond_by` but no `tutor_user_id`; the field error is on `["body","respond_by"]`. |
| `decline_note_too_long` | 422 | A decline note over 500 characters once trimmed; the field error is on `["body","note"]` (see Assignment offers). |
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
| `available_slots.user` | `GET /api/v1/available-slots` | 60 per 15 min | signed-in user | every request |
| `session_request.user` | `POST /api/v1/session-requests` | 10 per hour | signed-in user | every request |
| `change_request.user` | `POST /api/v1/change-requests` | 10 per hour | signed-in user | every request |

- **Fixed windows**, aligned to the Unix epoch in UTC (`:00`, `:15`, `:30`,
  `:45`; the full hour for registration). Up to twice a limit can pass across
  a window boundary; that is the accepted cost of fixed windows.
- **`429 rate_limited`** with `Retry-After`: the whole seconds until the
  exhausted window ends (rounded up, at least 1; at most 900 for login and
  reauthentication). The body never names the policy, the key or the account.
  Each `429` writes one `WARNING` line on the `app.security` logger with the
  policy code, method and path (no IP, username or user id) and no audit
  event.
- **Keyed by user** (ADR-0008 amendment, M4-14): a `requests` policy may be
  keyed by the signed-in user instead of the IP, so families behind the
  center's Wi-Fi don't share a budget. The dependency resolves the principal
  first (`401` before `429`) and ends the transaction the session lookup
  opened before counting. These two domain limits are separate from the
  general API limit (#163).
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
- **Types today:** `identity.registration` (see Registration requests),
  `staff.qualification` (#44, see Tutor qualifications),
  `scheduling.offer_reassignment` (#52, see Assignment offers) and
  `scheduling.occurrence_conflict` (#53, see Class series).

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

**Types today** (besides `dev.demo`): `staff.qualification_approved`,
`_rejected`, `_revoked` (#44, #198); `scheduling.offer_received`,
`_declined`, `_expired` (#52, #233); `scheduling.occurrence_skipped_closure`
(#232, see Class series); `scheduling.session_rescheduled`
`{class_session_id, starts_at}`, `scheduling.series_changed` `{series_id,
from_date, changed, cancelled}` (the resulting series; the recipient's own
counts) and `scheduling.series_cancelled` `{series_id, from_date}` (#54, see
Class sessions and Class series; no link yet); `scheduling.tutor_substituted`,
`scheduling.session_tutor_changed` and `scheduling.session_cancelled`, each
`{class_session_id, starts_at}` (#55, see Substitutions and Class sessions;
never the reason or a tutor's name; no link until #67). A substitution
offer's `scheduling.offer_received` / `_declined` / `_expired` params add
`kind = "substitution"` and `original_tutor_name`.
`scheduling.session_request_confirmed` and `_rejected` `{session_request_id,
student_name, subject_name, starts_at}` (#60, see Session requests; to the
requester only; never the reason).
`scheduling.change_request_approved` `{change_request_id, kind, starts_at,
new_starts_at, makeup_until, exception}` and
`scheduling.change_request_rejected` `{change_request_id, kind, starts_at}`
(#62, see Change requests; to the requester only; never the reason, a
tutor, a room or the classification; no link until #62b).
`staff.availability_change_approved` and `_rejected` `{change_request_id,
kind, affected_count}` (M4-18, to the tutor; never the reason; link: the
tutor's page) and `scheduling.reassignment_needed` `{tutor_user_id,
tutor_name, session_count, first_starts_at}` (M4-18, to the
`schedule.assign` holders; link: the calendar filtered by the tutor on the
first local date). See Tutor availability.

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
  cancelled ones included), blocks a zone change. M4-08 (#53) registers
  `scheduling.class_series`: any class series, even one with no session row
  yet (it starts after the horizon), blocks a zone change. Both read the
  zone with
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
- **Series occurrences** (M4-08b, #232): after a closure `POST`, or a
  `PATCH` that changed `starts_on`, `ends_on` or `is_active`, has
  committed, the route queues `scheduling:materialize_series` (a background
  task after the response; its `queueing_lock` keeps at most one queued, so
  a second write while one waits queues nothing). The job skips or restores
  the affected future series occurrences (see Class series). A rename or a
  type change queues nothing. A failed deferral is logged and never changes
  the response; the nightly run catches up. The responses are unchanged.
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
impact seam). Teaching limits, the required break and preferred classrooms
were added by M3-09b (#197, same files). Changes that affect scheduled
classes go through approval since M4-18 (#63; `availability_impact.py`,
`availability_change_requests.py`, `availability_change_decision.py`, the
handler in `app/workflows/availability_change.py`).

**Local wall-clock rule:** every availability value is a local `DATE` or
`TIME` of the center (`business_settings.time_zone`), never an instant. A
weekly window 16:00–20:00 means 16:00–20:00 local time on that weekday,
before and after a DST change. So availability is not "scheduled data" for
the time-zone change guard (#37).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/tutors/{user_id}/availability` | `require("tutor.view")` + the tutor profile record policy | `TutorAvailabilityDetail`: `weekly` (windows by weekday, then start; `HH:MM`), `exceptions` (by `starts_on`, whole days first, then `starts_at`), `limits` (`{daily_limit_minutes, weekly_limit_minutes, required_break_minutes}`, `null` = none), `preferred_classrooms` (`[{id, name, is_active}]` by name), `version` (`0` while nothing was ever changed), `pending_changes` (the pending change requests, oldest first) and `recent_changes` (the last 5 resolved ones, newest first; M4-18). |
| `PUT /api/v1/tutors/{user_id}/availability/weekly` | `require("tutor.view")`; the tutor themselves or `tutor.manage` | Body `{version, windows: [{weekday, starts_at, ends_at}]}`, at most 42 windows, the classroom window checks (`window_overlap` on the later item's `starts_at`). Replaces the windows; `200` with the detail, or `202` with an `AvailabilityChangeRequestItem` when it takes away time that scheduled classes use (nothing changes yet). |
| `PATCH /api/v1/tutors/{user_id}/availability/settings` | as above | Body `{version, daily_limit_minutes?, weekly_limit_minutes?, required_break_minutes?, preferred_classroom_ids?}`; fields not sent stay, a limit sent as `null` is cleared, `preferred_classroom_ids` replaces the list. `200` with the detail. No impact check. |
| `POST /api/v1/tutors/{user_id}/availability/exceptions` | as above | Body `{kind, starts_on, ends_on, starts_at?, ends_at?, note?}`. `201` with the `AvailabilityExceptionItem`, or `202` with an `AvailabilityChangeRequestItem` for time off over scheduled classes. |
| `DELETE /api/v1/tutors/{user_id}/availability/exceptions/{exception_id}` | as above | `204`, or `202` with an `AvailabilityChangeRequestItem` when the deleted extra hours are used by scheduled classes. An id of another tutor's exception is `404`. |
| `POST /api/v1/tutors/{user_id}/availability/change-requests/{change_request_id}/cancel` | as above | Body `{version}`. Withdraws a pending change request (M4-18); `200` with the item. Order `422` → `404` (tutor not visible, or not this tutor's request) → `403` → `409 version_conflict` → `409 availability_change_not_pending` (`details.status`). |
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
- **Changes that affect scheduled classes (M4-18 #63; spec §3).** The
  weekly `PUT` (only when it takes weekly time away:
  `availability.uncovered_ranges` is not empty), an `unavailable` exception
  `POST` and an `available` exception `DELETE` ask the injected
  `AvailabilityImpactSource` (`staff/assignment_impact.py`; `app.wiring`
  registers `scheduling.service.AVAILABILITY_IMPACT_SOURCE`, so `staff`
  never imports `scheduling`). Adding windows, adding extra hours or
  deleting time off never asks and always applies.
  - **Affected** (pure rule `staff/availability_impact.is_affected`): a
    session of the tutor that is `offered` or `confirmed`, starts after
    now, is covered by the tutor's **current** rules (windows and
    exceptions; center closures ignored) and not by the **proposed** ones,
    each judged on its local pieces in the center zone (the DST gap and
    repeated hour included). A session booked outside the tutor's
    availability with a `tutor_unavailable` override is never affected;
    a change that removes and re-adds the same time affects nothing.
    Occurrences beyond the series horizon don't exist yet: the nightly
    materialization checks the tutor's availability when it creates them.
    The source takes the tutor lock and reads the tutor's future sessions
    (bounded to the exception's dates) in at most 6 statements, whatever
    their number.
  - **No affected session:** the change applies as before (`200`/`201`/
    `204`), even while other requests are pending.
  - **One or more:** nothing changes; a `tutor_availability_change_requests`
    row stores the change (`payload`: the weekly `{base_version, windows}`,
    the new exception's fields, or `{exception_id, exception}`) and the
    snapshot of the affected sessions, with a `staff.availability_change`
    inbox item (requester = the caller; priority `high` when the earliest
    affected session starts within 24 hours, else `normal`). `202` with the
    `AvailabilityChangeRequestItem`: `{id, kind (weekly | exception_create |
    exception_delete), status (pending | approved | rejected | cancelled),
    change {windows | null, exception {id | null, kind, starts_on, ends_on,
    starts_at, ends_at, note} | null}, affected_count, affected_sessions
    [{id, starts_at, ends_at, subject_name, state}] (the first 20 of the
    snapshot), requested_by {id, display_name}, created_at, resolved_at,
    rejection_reason, approval_request_id, version}`. An Owner or
    Administrator submitting for a tutor gets `202` too and may approve
    their own request.
  - **Deciding** (`/api/v1/approval-requests/{id}/approve` and `/reject`,
    review permission `tutor.manage`; no request-changes; a rejection needs
    a reason). Approve, in the inbox's Unit of Work: the request `FOR
    UPDATE` (`409 approval_transition_invalid` unless pending) → the
    settings row `FOR UPDATE` (weekly) and the scheduling tutor lock →
    `409 availability_change_stale` when a weekly request's `base_version`
    is not the current `version` or the exception to delete is gone (the
    item stays pending) → the affected sessions are **recomputed** with the
    rules as they are now → the change is applied by the direct code (same
    audit events, the approver as actor; the weekly `version` bumps) → each
    recomputed session is flagged for reassignment (see Class sessions): an
    `offered` one has its pending offer withdrawn and becomes `unassigned`,
    a `confirmed` one keeps its state and tutor → `approved` → the
    notifications. A recompute with no session still applies the change.
    Reject: `rejected` with the reason (shown in `recent_changes`); nothing
    else changes.
  - **Withdraw:** the cancel route above; the inbox item is cancelled in
    the same transaction (lock order: the inbox item, then the request, as
    a decision).
  - **Notifications:** `staff.availability_change_approved` / `_rejected`
    `{change_request_id, kind, affected_count}` to the tutor (never the
    reason); `scheduling.reassignment_needed` `{tutor_user_id, tutor_name,
    session_count, first_starts_at}` once per approval that flagged
    sessions, to every active holder of `schedule.assign`. Families are not
    notified.
  - **Tutor lock** (`scheduling.locks.lock_tutor`, a transaction advisory
    lock): taken before the tutor's availability is read by the impact
    source, the approval, the class session create with a tutor, the
    re-offer, the substitution (for the substitute) and the series offer,
    so a session can't be booked into time that is being removed and
    escape both checks.
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

### Class sessions

Implemented by M4-03 (#48; spec §3, §15 W1.1–W1.2; ADR-0005 D1, D2, D7,
D9) in `apps/api/app/modules/scheduling/` (`booking.py`: create and read;
`conflicts.py`: the codes and pure rules; `locks.py`: the student lock).
Overrides of soft conflicts: M4-04 (#49; spec §3, W1.6), `overrides.py`.
Workload warnings and suggested times: M4-05 (#50; spec §3), `workload.py`
and `suggestions.py` (pure rules), `workload_sessions.py` and
`slot_search.py` (the reads). The create takes one-off **individual**
sessions; group sessions are the occurrences of a tutoring group's series
(M4-11, Tutoring groups below) and courses come later. Offers (M4-07,
#52): see Assignment offers below. The
calendar's range read and tutor options (M4-12, #57) are in
`calendar_read.py` and `markers.py`. Tutor suggestions (M4-06, #51; spec
§3, W1.3): `eligibility.py` (the pure rule) and `tutor_suggestions.py`.
Recurring series (M4-08, #53) are in Class series below; their occurrences
are class sessions too. The conflict checks of every write path run in one
collector, `checks.collect_conflicts`.

`schedule.assign` (M4-06, ADR-0005 D9; not delegable) is held by the Owner,
Administrators and Academic coordinators: it covers seeing tutor
eligibility and replacement suggestions, and (M4-07) offering a session to
a tutor. Finance staff, tutors, students and guardians don't hold it.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/class-sessions` | `require("schedule.manage")` (Owner, Administrator); an `override` also needs `schedule.override` | Body below. `201` with `ClassSessionDetail` plus `warnings` (the workload warnings below; `[]` without a tutor). |
| `POST /api/v1/class-sessions/suggestions` | `require("schedule.manage")` (Owner, Administrator) | Read-only: free times for the same session (below). `200` with `SlotSuggestions`. A `GET` on the path is `405`. |
| `GET /api/v1/class-sessions?from=…&to=…` | `require("schedule.view")` (same four roles) | The calendar's range read (#57), below. |
| `GET /api/v1/class-sessions/{class_session_id}` | `require("schedule.view")` (Owner, Administrator, Academic coordinator, Finance staff) | `ClassSessionDetail`; `404 not_found` for an unknown id. Every holder sees every session and its overrides (no record policy for staff). At most 7 statements. |
| `GET /api/v1/schedule/tutor-options?q=…&limit=…` | `require("schedule.view")` (same four roles) | The calendar's tutor filter (#57), below. |
| `GET /api/v1/tutor-suggestions?subject_id=…&starts_at=…&ends_at=…` | `require("schedule.assign")` (Owner, Administrator, Academic coordinator) | The eligible tutors for a new slot (#51), below. `200` with `TutorSuggestionList`. |
| `GET /api/v1/class-sessions/{class_session_id}/replacement-tutors` | `require("schedule.assign")` (same three roles) | Replacement tutors for a session, never its current tutor (#51), below. |
| `POST`/`GET /api/v1/class-sessions/{class_session_id}/offers` | `schedule.assign` / `schedule.view` | Offer an `unassigned` session to a tutor; the session's offer history (M4-07, see Assignment offers). |
| `POST /api/v1/class-sessions/{class_session_id}/conflict-overrides` | `require("schedule.manage")` plus `schedule.override` (checked after the `404`) | Override the soft conflicts of one `conflict` series occurrence and schedule it (M4-09c); `200` with `ClassSessionDetail`. See Class series, "Series overrides". |
| `PATCH /api/v1/class-sessions/{class_session_id}` | `require("schedule.manage")` (Owner, Administrator); an `override` also needs `schedule.override` (checked before anything is read) | Move one session or series occurrence (M4-09, #54; the calendar's drag and drop, #58), see "Moving a session" below. `200` with `ClassSessionDetail` plus the tutor's workload `warnings`. |

**Body** (unknown fields are `422`):

```json
{"kind": "individual", "student_id": "…", "subject_id": "…",
 "tutor_user_id": "…" | null, "classroom_id": "…" | null,
 "starts_at": "2026-11-10T16:00:00-06:00", "ends_at": "2026-11-10T17:00:00-06:00",
 "override": {"codes": ["outside_operating_hours"], "reason": "…"} | null,
 "respond_by": "2026-11-09T12:00:00-06:00" | null}
```

`starts_at`/`ends_at` are instants with an offset; responses give them in
UTC (`Z`). `ClassSessionDetail` is `{id, kind, state, subject {id, name,
education_level_name, is_active}, tutor {id, display_name} | null,
classroom {id, name, is_active} | null, participants [{student_id,
display_name, is_active}], starts_at, ends_at, scheduled_minutes, version,
created_at, updated_at, overrides [{id, codes, reason, actor {id,
display_name}, created_at}], series_id, original_start, is_exception,
conflict_codes, pending_offer {id, tutor {id, display_name}, respond_by} |
null}` (overrides oldest first, `[]` when none; `pending_offer` is the
pending offer of an `offered` session, M4-07); a subject is shown inactive
when it or its level is. `respond_by` is taken only with a tutor (see
Assignment offers). `series_id` and `original_start` (the occurrence's
instant in its series rule, kept when it is moved) are `null` for a one-off
session; `is_exception` marks an occurrence edited apart from its series
(M4-09); `conflict_codes` lists the codes that put a generated occurrence in
`conflict` (informational, kept after it leaves `conflict`; `[]` otherwise).
`group_id` is the tutoring group of a `group` session (M4-11), `null`
otherwise; `cohort_id` the course cohort of a `course` session (M4-20),
`null` otherwise.

**Order of the create:**

1. `401`/`403`, then `422` for the schema (the `override` rules below
   included);
2. `403 permission_denied` when `override` is sent and the user lacks
   `schedule.override`, even if the request would have no conflict; nothing
   is read or locked. The effective permissions count (roles plus active
   delegations), so a just granted or revoked delegation applies to the next
   request;
3. the per-student advisory lock (below);
4. the center time zone, read `FOR SHARE` (a zone change waits); with a
   tutor, then the tutor lock (M4-18: serialized with a change of that
   tutor's availability);
5. `422` for the bounds (#47), as a field error on `ends_at`, except
   `naive_datetime` and `not_whole_minutes`, which go on the field at fault:
   `naive_datetime`, `not_whole_minutes`, `ends_before_start`,
   `duration_out_of_range` (15–480 elapsed minutes), `crosses_local_date`
   (start and end on different local dates in the center zone; an end at
   the next midnight is allowed);
6. `422` for the references, every one in one response: `student_unknown`,
   `student_inactive` (`student_id`); `subject_unknown`, `subject_inactive`
   (the subject or its level; `subject_id`); `tutor_unknown` (no such user,
   or not a tutor), `tutor_inactive` (any state but `active`, a pending
   invitee included; `tutor_user_id`); `classroom_unknown`,
   `classroom_inactive` (`classroom_id`); then `422` for `respond_by`
   (`respond_by_requires_tutor` without a tutor, `respond_by_invalid`; M4-07);
7. every conflict check below, then the override decision (below): `409
   scheduling_conflict` with **all** of them, hard first, then in this
   table's order, unless there is none or the override covers them;
8. the session and its participant are stored. A concurrent create that
   wins the tutor or room `EXCLUDE` constraint first makes this one `409
   scheduling_conflict` with that single hard conflict (its
   `class_session_ids` is `[]`). Just before, the tutor's workload warnings
   are computed (below; at most two statements with a tutor, none
   without);
   With a tutor, a start that is not after now is `409
   class_session_not_offerable` (`details.reason = "started"`; reachable
   with an overridden `notice_window`), right after the override decision;
9. with overridden conflicts, the override row and its audit event, in the
   same transaction;
10. with a tutor (M4-07), the `pending` offer and the tutor's
    `scheduling.offer_received` notification, in the same transaction (see
    Assignment offers).

**Conflicts.** The tutor checks run only with a tutor, the room checks
only with a room. Local values are in the center zone; a local end at
midnight is never inside a window or the opening hours.

| Code | Severity | When | `details` |
| ---- | -------- | ---- | --------- |
| `tutor_overlap` | hard | The tutor has an `offered`, `confirmed` or `completed` session that overlaps (`[)` ranges: back-to-back is fine). | `{class_session_ids}` (up to 10) |
| `room_overlap` | hard | The room has an `unassigned`, `offered`, `confirmed` or `completed` session that overlaps. | `{classroom_id, class_session_ids}` |
| `student_overlap` | hard | The student is in a session in those four states that overlaps. | `{student_id, class_session_ids}` |
| `room_capacity_exceeded` | hard | Never for an individual session (a room holds at least 1); groups (M4-11). | — |
| `tutor_unavailable` | soft | The tutor is outside their weekly availability or on time off (M3-09); a closure is reported only as `center_closed`. | `{reasons}` (`time_off`, `outside_weekly_availability`) |
| `room_unavailable` | soft | The room has windows and they don't cover the time. | `{classroom_id}` |
| `center_closed` | soft | An active closure contains the local date. | `{date}` |
| `outside_operating_hours` | soft | The weekday's opening hours don't cover the time; a weekday without hours is closed. | `{date, opens_at, closes_at}` (`null` on a closed weekday) |
| `tutor_not_qualified` | soft | The tutor has no approved qualification for the subject (a revoked one doesn't count). | `{subject_id}` |
| `group_capacity_exceeded` | soft | Groups and courses (M4-11, M4-20). | — |
| `notice_window` | soft | The start gives less than the booking notice of the scheduling policy in force now (a start in the past included; exactly the notice is fine). | `{booking_notice_hours}` |

**Workload warnings** (M4-05, ADR-0005 warnings). They never block, never
need an override and come only with a `2xx` (`warnings [{code, details}]`;
never in the `409` body): the `201` of a create (an overridden one
included) and each suggested time. They count the tutor's `offered`,
`confirmed` and `completed` sessions plus the new one, once (`unassigned`,
`cancelled`, `skipped_closure` and `conflict` never count), against the
tutor's teaching limits (M3-09b; `null` is no limit). Local dates are in the
center zone; a session counts on its start's local date.

| Code | When | `details` |
| ---- | ---- | --------- |
| `daily_limit_exceeded` | The day's scheduled minutes, the new session included, are **above** `daily_limit_minutes` (exactly the limit doesn't warn). | `{date, limit_minutes, scheduled_minutes}` |
| `weekly_limit_exceeded` | The same over the **ISO week** (Monday to Sunday, local dates) holding the session, against `weekly_limit_minutes`. | `{week_start, limit_minutes, scheduled_minutes}` (`week_start` is the Monday) |
| `break_too_short` | `required_break_minutes` is above 0, and the gap (elapsed minutes, across midnight too) to the session that ends last at or before the start, or to the one that starts first at or after the end, is **below** it (back-to-back warns; exactly the break doesn't). One warning per neighbour, the earlier first. | `{class_session_id, gap_minutes, required_break_minutes}` |

Order: daily, weekly, then break. Warnings are computed per response and
never stored, audited or notified. They are **not locked**: two concurrent
creates for the same tutor on the same day may each miss the other's
minutes (the overlap guarantees, the student lock and the `EXCLUDE`
constraints, are unchanged).

**Suggested times** (`POST /api/v1/class-sessions/suggestions`, M4-05). The
body is the create's without `override` (an `override` member is `422
extra_forbidden`) plus the search:

```json
{"kind": "individual", "student_id": "…", "subject_id": "…",
 "tutor_user_id": "…" | null, "classroom_id": "…" | null,
 "starts_at": "2026-11-10T16:00:00-06:00", "ends_at": "2026-11-10T17:00:00-06:00",
 "limit": 5, "search_from": "2026-11-10", "search_to": "2026-11-16"}
```

- `limit`: 1–10, default 5;
- `search_from` / `search_to`: local dates (center zone), both optional.
  `search_from` defaults to the local date of `starts_at`, `search_to` to
  `search_from` + 6 days. `422` on `search_to`: `search_range_invalid`
  (before `search_from`) or `search_range_too_long` (more than 14 dates); a
  defaulted date is checked the same way;
- `starts_at` / `ends_at` give only the duration and the ranking anchor.

Order: `401`/`403` → `422` schema (and the window when both dates are sent)
→ the zone (a plain read: no `FOR SHARE`, no lock) → `422` bounds (as the
create) → `422` window (defaulted dates) → `422` references (as the create,
all in one response) → `200`. Nothing is inserted, updated, audited or
notified.

Only the **time** changes: the student, subject, tutor, classroom and
duration stay as requested (preferred classrooms are not used). Candidate
starts are a 15-minute grid of local wall-clock time (`:00`, `:15`, `:30`,
`:45`) on every date of the window, each lasting the requested elapsed
minutes. A local start in a spring-forward gap is skipped; an ambiguous
(fall-back) one gives only its first instant (`fold=0`). A candidate that
crosses the local date is skipped, and the requested start itself is never
returned. A candidate is returned only if **every** conflict check of the
create (the table above) finds nothing; the checks run in memory over data
read once per request, so the route runs at most 20 statements whatever the
window. A tutor not qualified for the subject gives no slot. The passing
candidates are ordered by the absolute distance between their start and the
requested start (a tie goes to the earlier start), and the first `limit`
are returned; warnings don't affect the order.

`SlotSuggestions` is `{search_from, search_to, limit, slots [{starts_at,
ends_at, warnings}]}` (instants in UTC; 0 to `limit` slots; an empty list is
a normal `200`). Each slot carries the warnings a create at that time would
get. The route only reads: the create still rechecks everything when a slot
is chosen.

**Initial state:** with a tutor the session is created `offered` to them,
without one `unassigned` (ADR-0005 D2). No offer row, notification or inbox
item exists yet (M4-07); the request never waits for the tutor.

**Student lock:** before reading anything, the create takes
`pg_advisory_xact_lock(480301, hashtext(student_id::text))` for each
student, in ascending id order (`scheduling.locks.lock_students`). Every
writer that adds a participant or moves a session in time must take it
first, so two concurrent bookings of one student can never both succeed.

**Overrides** (M4-04, ADR-0005 "Conflict wire format and overrides"). A
caller with `schedule.override` (the Owner by role; an Administrator only
while an Owner's delegation is active) resends the same create with
`override: {codes, reason}`:

- `codes`: 1–7 soft codes, no duplicates. `422 validation_error` field
  errors: `codes_required` (missing or empty) and `duplicate_code` on
  `override.codes`; `code_not_overridable` (a hard code) and `code_unknown`
  (a warning code or any other string) on `override.codes[i]`;
- `reason`: trimmed, 1–1000 characters: `reason_required` (missing, empty or
  whitespace only) and `reason_too_long` on `override.reason`.

The server **recomputes** every conflict in the create's transaction, under
the student lock, so an override never covers a conflict the client hasn't
seen. Then:

- any hard conflict → `409` with the complete list (hard and soft); the
  override changes nothing;
- a soft conflict whose code isn't listed → `409` with the complete list;
- every conflict soft and listed → created. The overridden conflicts are the
  computed ones (code and details), not the client's list;
- listed codes that don't occur are ignored: never stored or audited;
- no conflict at all → created as without an override: no override row and
  no override event.

The `409` body is the same as without an override (it doesn't echo the
override). Each override is one append-only `class_session_overrides` row
(`codes` in rule order, `conflicts [{code, details}]`, the trimmed `reason`,
`actor_user_id`; `UPDATE` and `DELETE` are refused, SQLSTATE 42501). No
notification is sent.

**Audit:** the automatic `create` events of `class_sessions` and
`class_session_participants`, with the actor and the request ID. With an
override, also the named event `scheduling.session_override` on the session
(`record_type` `class_sessions`), `details {override_id, codes, conflicts,
reason}`, same actor and request ID; `GET /api/v1/audit-events` filtered by
the session shows it to holders of `audit.view`.

**Range read** (`GET /api/v1/class-sessions`, #57). Query (unknown
parameters, a malformed id or an unknown `kind` are `422
validation_error`):

| Parameter | Notes |
| --------- | ----- |
| `from`, `to` | Required instants with an offset. A naive value is `naive_datetime` on its field; `to` must be after `from` (`range_invalid` on `to`) and at most 45 days later (`range_too_long` on `to`). |
| `tutor_user_id`, `student_id`, `classroom_id` | Optional, one id each: the current tutor, a participant, the room. An unknown id matches nothing (`200`, no items). |
| `kind` | Optional: `individual`, `group` or `course`. |
| `include_cancelled` | Optional, default `false`: `cancelled` sessions only when `true`. Every other state is always returned (`skipped_closure` and `conflict` included). |

A session matches when it overlaps the half-open range: `starts_at < to`
and `ends_at > from` (a session ending exactly at `from` or starting exactly
at `to` is left out). Filters combine with AND. The response is
`{"items": [ClassSessionCalendarItem, …], "truncated": false}`, by
`starts_at` then `id`, at most 1000 items; `truncated` is `true` when more
matched (narrow the range or the filters; there is no cursor).
`ClassSessionCalendarItem` is `{id, kind, state, version, series_id,
starts_at, ends_at, subject {id, name, education_level_name}, tutor {id,
display_name} | null, classroom {id, name} | null, participants
[{student_id, display_name}], markers}`. `version` is what a move sends
(#58); `series_id` is the weekly series of an occurrence, `null` for a
one-off session. The read takes at most 10 statements whatever the item count, and
writes nothing (no lock, no audit event). Tutors, students and guardians get
`403` (their own calendars come with M4-22).

`markers` are computed on every read (never stored), in this order:

| Marker | When |
| ------ | ---- |
| `center_closed` | The session is `unassigned`, `offered` or `confirmed` and its local date (center zone) is in an active closure. A one-off session is only marked, never changed (ADR-0005 D6). |
| `tutor_not_qualified` | The session is `offered` or `confirmed`, has a tutor, starts now or later, and the tutor holds no approved qualification for its subject (#198). Past and `completed` sessions never get it. |
| `overridden` | The session has at least one override row (booked or moved with an override of a soft rule, #58), in any state: a historical fact, so `completed` and `cancelled` sessions keep it. Computed in the sessions statement (no extra statement). |
| `reassignment_needed` | The session has an open reassignment flag (its tutor's availability changed, M4-18; see below). Computed in the sessions statement (no extra statement). Staff-only. |

**Tutor options** (`GET /api/v1/schedule/tutor-options`, #57): `q`
(optional, 1–100 characters, trimmed) and `limit` (1–50, default 20). Returns
`{"items": [{id, display_name, state}]}`: the users holding the `tutor` role
whose username or display name contains `q` (not case-sensitive), by display
name, inactive ones included (the UI labels them). No contact data, so
Finance staff, who don't hold `tutor.view`, can filter the calendar by
tutor. At most 3 statements.

**Tutor suggestions** (#51). Both routes are read-only and **advisory**:
nothing is assigned, offered, locked, audited or notified, and creating or
offering a session re-runs every check. A tutor is **eligible** for a slot
(subject, start, end, optional classroom) when they:

1. hold an **approved** qualification for the subject (pending, rejected,
   cancelled and revoked ones never count, checked live; a subject belongs
   to one education level, so this is also the level check);
2. hold the `tutor` role with an `active` account (suspended, archived and
   pending invitees are left out);
3. are **available** for the whole slot: their weekly windows and dated
   exceptions (`time_off`, `outside_weekly_availability`; a tutor without
   windows is never available). A center closure, operating hours, room
   windows and the booking notice are **slot** conflicts, not tutor ones:
   they never empty the list (the create reports them);
4. have **no overlapping** session as tutor in `offered`, `confirmed` or
   `completed` (`starts_at < end AND ends_at > start`; back-to-back is fine;
   `unassigned`, `cancelled`, `skipped_closure` and `conflict` sessions
   never block).

Teaching limits, breaks, the student's overlap and level, and earlier
declined offers are not applied.

**Workload ranking rule.** For each eligible tutor, `weekly_minutes` is the
sum of `scheduled_minutes` of their sessions in `offered`, `confirmed` or
`completed` whose **local start date** (center zone) is in the ISO week
(Monday to Sunday) of the slot's local date, and `daily_minutes` the same
for the slot's local date (the bounds are local midnights, so a DST week has
167 or 169 hours). Tutors are ordered by `weekly_minutes` ascending, then
`daily_minutes` ascending, then those who prefer the slot's classroom first
(#197 preferred classrooms; without a classroom nobody does), then display
name (case-folded), then user id. The teaching limits are returned for
display only: they never drop or reorder a tutor.

`GET /api/v1/tutor-suggestions` query: `subject_id` (required), `starts_at`
and `ends_at` (required instants with an offset; send `+` percent-encoded),
`classroom_id` (optional), `limit` (1–50, default 10); unknown parameters
are `422`. Order: `401`/`403` → `422` for a missing or malformed parameter or
`limit` → `422` for the bounds, as the create (`naive_datetime`,
`not_whole_minutes`, `ends_before_start`, `duration_out_of_range`,
`crosses_local_date`; on `["query", "starts_at"]` or `["query",
"ends_at"]`) → `422` for the references, all at once (`subject_unknown` /
`subject_inactive` on `subject_id`, `classroom_unknown` /
`classroom_inactive` on `classroom_id`) → `200`.

`GET /api/v1/class-sessions/{class_session_id}/replacement-tutors` query:
`limit` (1–50, default 10). The slot is the session's own subject, times and
classroom; the session itself is left out of the overlap and the workload,
and its current tutor (an `offered`, `confirmed` or `conflict` session's
`tutor_user_id`) is never listed. An `unassigned` session excludes nobody.
A session whose subject became inactive still gets suggestions. Order:
`401`/`403` → `422` (`limit`) → `404 not_found` → `409
class_session_not_assignable` (`completed`, `cancelled`,
`skipped_closure`) → `200`.

Response (`TutorSuggestionList`):

```json
{"items": [{"rank": 1,
            "tutor": {"id": "…", "display_name": "…"},
            "weekly_minutes": 120, "daily_minutes": 60,
            "weekly_limit_minutes": 900, "daily_limit_minutes": null,
            "prefers_classroom": false}],
 "eligible_count": 3}
```

`items` holds the first `limit` eligible tutors in ranking order (`rank`
from 1); `eligible_count` counts them all. Nobody eligible is `200` with
`items: []` and `eligible_count: 0`; ineligible tutors and their reasons are
never listed. `scheduling.service.suggest_tutors` is the one implementation
(for M4-07 offers and M4-10 substitutions too); it takes at most `8 + 6 × C`
statements, `C` being the qualified active tutors (tutors without the
qualification cost nothing). The web app's "Schedule a session" form has a
"Suggest tutors" button for holders of `schedule.assign`; the session page
(`/schedule/sessions/:id`, M4-07) uses the replacement list in its "Offer to
a tutor" panel.

Overrides written by a series write or the conflict-override route (M4-09c)
carry the series in `class_series_id` (context, not a second subject; `null`
for a one-off session's override); see Class series, "Series overrides".

#### Moving a session

`PATCH /api/v1/class-sessions/{class_session_id}` (M4-09, #54 C1;
`scheduling/session_edit.py`) moves a one-off session or one occurrence of
a series; the calendar's drag and drop and its Reschedule dialog send it
too (M4-13, #58). Body (unknown fields are `422`, `tutor_user_id`
included):

```json
{"version": 3, "starts_at": "2026-12-07T18:00:00-06:00",
 "ends_at": "2026-12-07T19:00:00-06:00", "classroom_id": "…" | null,
 "override": {"codes": ["outside_operating_hours"], "reason": "…"} | null}
```

`classroom_id` absent keeps the room; `null` removes it. The tutor, the
subject and the students never change here (offers and substitutions do
that). `override` is the create's (#49): 1–7 unique soft codes and a reason
(1–1000 characters once trimmed), with the same `422` codes
(`reason_required`, `reason_too_long`, `codes_required`, `duplicate_code`,
`code_not_overridable`, `code_unknown`). **Order:**

1. `401`/`403` (`schedule.manage`), then `422` for the schema (the
   `override` included);
2. `403 permission_denied` when `override` is sent and the caller lacks
   `schedule.override` (roles plus delegations), even for a move without a
   conflict; nothing is read or locked;
3. `404 not_found` for an unknown id;
4. the student locks, the zone `FOR SHARE`, for a group occurrence the
   group row `FOR UPDATE`, for an occurrence its series row `FOR SHARE`
   (its version is not bumped), then the session `FOR UPDATE`;
5. `409 version_conflict` on the session `version`;
6. `409 session_not_editable`: the session has begun (`starts_at <= now`,
   `details.reason = "started"`), or it is `completed`, `cancelled` or
   `skipped_closure` (`details.state`; a makeup is an ordinary one-off
   session);
7. `422` for the bounds (the create's codes and fields);
8. `422` for the references (the create's codes: the students, the subject,
   the tutor the session keeps and the room), in one response;
9. `422 session_unchanged` when the times and the room are the same (an
   `override` doesn't make an unchanged body valid);
10. every Class sessions check at the new time with the session's own tutor
    (for a `conflict` occurrence, the tutor its series assigns on its date),
    `center_closed` included (moving onto a closure date is deliberate), the
    session itself left out of the overlaps, recomputed under the locks;
11. the override decision (as the create's): any hard conflict, or a soft
    one the `override` doesn't list, is `409 scheduling_conflict` with the
    complete list (the #48 body; the override is not echoed); listed codes
    that don't occur are ignored; with no conflict nothing is overridden;
12. the tutor's workload warnings at the new time (as the create's, #50),
    the session itself counted once (its old row is left out); `[]` without
    a tutor; never in a `409`; at most 2 statements;
13. the update and its side effects (below);
14. with something overridden, one `class_session_overrides` row (an
    occurrence's carries its own series in `class_series_id`) and the
    named audit event `scheduling.session_override`, in the same
    transaction;
15. `200` with `ClassSessionDetail` (its `overrides` included) and
    `warnings`.

**No carry-forward:** an override covers the move it was sent with. A
session booked or moved with an override and moved again to a time where
the same soft conflict occurs needs a new override, or the answer is `409`;
earlier override rows stay as history. No notification is sent for an
override.

An **occurrence** becomes an exception (`is_exception = true`) and keeps
its `original_start`, so the nightly job never recreates its date and never
touches the row again. A `conflict` occurrence that passes takes the
occurrence state of its date (`unassigned`, `offered` with the pending
series offer, or `confirmed`; `conflict_codes` are kept) and its
`scheduling.occurrence_conflict` request is cancelled by the system.
`offered` and `confirmed` sessions keep their state and tutor; a pending
offer's `respond_by` is capped at its first covered start (ADR-0005 D3).
`scheduling.session_rescheduled` goes to the tutor of an `offered` or
`confirmed` session and, for `confirmed`, to the student's login and its
linked guardians (see Notifications). An `EXCLUDE` race is `409
scheduling_conflict` with that one conflict. A `confirmed` session's pending
substitution is withdrawn (#55 G3; so are the substitutions of rows a series
edit moves to a new slot or room, skips or cancels, and of rows the closure
reconcile skips).

The service is `scheduling.service.update_class_session(session, id, body,
*, actor_id, notice_reference_at=None, exclude_recipient_ids=())`. An
approved #62 reschedule request (see Change requests) passes the request's
`created_at` as `notice_reference_at` (the booking notice is measured from
it, with the policy in force then; every other check stays live) and the
requester in `exclude_recipient_ids`. `PATCH` passes neither.

#### Cancelling a session

`POST /api/v1/class-sessions/{class_session_id}/cancel` (M4-10, #55 C;
`require("schedule.manage")`: Owners and Administrators; a Coordinator gets
`403`) cancels one one-off session or one occurrence of a series. Body
(`extra="forbid"`): `{version, reason}`, `reason` trimmed, 1–1000
characters (`422 reason_required` / `reason_too_long`). Order:

1. `401`/`403`, then `422`;
2. the session, unlocked → `404 not_found`;
3. the locks: the series row `FOR UPDATE` when the row belongs to one (the
   series-offer lock order), then the session `FOR UPDATE`, then the
   offers;
4. `409 version_conflict`;
5. `409 class_session_not_cancellable`: `details.reason = "state"` (with
   `details.state`) for `completed`, `cancelled` and `skipped_closure`;
   `started` when `starts_at <= now` (what happens during or after a lesson
   is attendance, M5);
6. every pending **session-scope** offer of either kind is `withdrawn` (an
   assignment offer of an `offered` session, a pending substitution); a
   pending **series** offer covering the row is left alone and its answer
   skips the row;
7. `→ cancelled` with `cancelled_at` = now and the reason;
8. the open `scheduling.offer_reassignment` items of the session's
   session-scope offers and the open `scheduling.occurrence_conflict` item of
   a `conflict` row are cancelled by the caller;
9. the named audit event `scheduling.session_cancelled`
   `{class_session_id, series_id, previous_state, reason,
   withdrawn_offer_ids, cancelled_request_ids, change_request_id}` (plus the
   automatic update event; `change_request_id` is the approved #62 request,
   else `null`);
10. `scheduling.session_cancelled` to the tutor of an `offered` or
    `confirmed` session, to a pending substitute, and for `confirmed` only to
    the students' logins and their linked guardians (from the state before
    the cancel; never the actor; nothing for `unassigned` or `conflict`
    rows);
11. the cancellation steps of `app/workflows/session_cancellation.py`
    (`on_session_cancelled`, registered by `app.wiring`; today a no-op that
    M6-14 billing and M7-04 pay extend), in the same transaction. No policy
    classification is stored for a center cancellation;
12. `200` with the session (`cancellation {cancelled_at, reason}`).

A cancelled occurrence keeps `series_id` and `original_start`: the series is
unchanged and the nightly job never re-creates the date. The service is
`scheduling.service.cancel_class_session(session, id, *, expected_version,
reason, by_user_id, initiated_by="center" | "family" | "tutor",
change_request_id=None, exclude_recipient_ids=())`; #62 (M4-17) reuses it
for approved family and tutor requests: the stored reason is then the system
code `family_request` or `tutor_request` (the web translates it), the seam
receives the `change_request_id`, and the requester is left out of
`scheduling.session_cancelled` (they get their own decision notification).

`ClassSessionDetail.pending_change_request_id` (#62 G2) is the session's
pending change request, or `null` (read with the session row: no extra
statement).

**Reassignment flags** (M4-18 #63 E). Approving an availability change that
affects sessions (see Tutor availability) adds one
`class_session_reassignment_flags` row per affected session (append-only:
the session, the tutor whose availability changed, `reason`
`availability_change`, the change request id, the approver). A flag is
**open** while the session starts in the future, is `unassigned`, `offered`
or `confirmed`, and its tutor is still the flagged tutor or none; so
re-offering the session to another tutor, substituting the tutor,
cancelling the session or the session starting closes it, with nothing to
resolve by hand. `ClassSessionDetail.reassignment` is the newest open flag
`{reason, tutor {id, display_name}, flagged_at, change_request_id} | null`
(read with the session row: the detail stays at seven statements), and the
calendar marks the session `reassignment_needed`. Staff-only: family views
never show it.

`ClassSessionDetail` also carries `pending_substitution {id, substitute {id,
display_name}, respond_by} | null` and `cancellation {cancelled_at, reason}
| null` (#55 E3; read with the session row, the detail stays at seven
statements). `pending_offer` is the pending **assignment** offer only.

### Assignment offers

Implemented by M4-07 (#52; spec §3, §9, §15 W1.3–W1.4; ADR-0005 D2, D3,
D9) in `apps/api/app/modules/scheduling/`: `offers.py` (the pure rules, the
invariant and the lock order), `offer_flow.py` (writes and reads),
`offer_records.py` (storing offers, notifications), `offer_approval.py`
(the inbox type) and `tasks.py` (the expiry task). Series offers (M4-07b,
#233) are described under "Series offers" below (`series_offers.py`).

**Model.** `assignment_offers` (audited, versioned): `scope` (`session`, or
`series` with `class_series_id` and the local `from_date`, both set exactly
for a series offer; at most one `pending` series offer per series),
`tutor_user_id`, `status` (`pending` → `accepted` | `declined` | `expired`
| `withdrawn`; the other four are final), `offered_at`,
`offered_by_user_id`, `respond_by` (after `offered_at`), `responded_at`
(set exactly when not `pending`), `decline_note` (only `declined`, at most
500 characters). `assignment_offer_sessions` (audited): the covered
sessions, one row per offer and session (a session offer has exactly one).
**Invariant:** a session is `offered` iff exactly one `pending` offer
covers it, made to the session's tutor (both scopes). **Lock order:** every
offer writer locks the covered sessions `FOR UPDATE` (ascending id), then
the offer `FOR UPDATE`; a series-scope writer takes the series row `FOR
UPDATE` before them (see "Series offers").

**Deadline.** `respond_by` defaults to the scheduling policy's
`response_hours` (48 by default) after `offered_at`, but never after the
session's start (`min(offered_at + response_hours, starts_at)`; the policy
version in force at `offered_at`). An earlier one may be requested: after
now and no later than the default, else `422 respond_by_invalid`. Both are
truncated to whole minutes; a naive value is `422`. An offer is overdue
from `respond_by` on (`now >= respond_by`).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/class-sessions` (extended) | `schedule.manage` | With a tutor: the session is created `offered` with its pending offer (optional `respond_by`) and the tutor is notified. |
| `POST /api/v1/class-sessions/{class_session_id}/offers` | `require("schedule.assign")` (Owner, Administrator, Academic coordinator); an `override` also needs `schedule.override` | Offer an `unassigned` session; `201` with `{offer: AssignmentOfferItem, warnings}`. |
| `GET /api/v1/class-sessions/{class_session_id}/offers` | `require("schedule.view")` (Owner, Administrator, Academic coordinator, Finance staff) | `{items: [AssignmentOfferItem]}`, newest first, decline notes included; `404` for an unknown session. |
| `POST /api/v1/class-series/{class_series_id}/offers` | `require("schedule.assign")` (Owner, Administrator, Academic coordinator) | Offer a series without a tutor from a date (M4-07b); `201` with `{offer: AssignmentOfferItem, warnings: []}`. See "Series offers". |
| `GET /api/v1/assignment-offers?status=pending\|answered\|all&limit&offset` | `authenticated()`; only the caller's own offers | Default `pending` (by `respond_by`); `answered` (the four final statuses) and `all` newest first. `limit` 1–100 (default 20), `offset`; `{items, total, limit, offset}`. Anyone without offers gets an empty page. At most 7 statements whatever the page size. |
| `POST /api/v1/assignment-offers/{offer_id}/accept` | `authenticated()`; only the offered tutor | `200` with the `AssignmentOfferItem`. |
| `POST /api/v1/assignment-offers/{offer_id}/decline` | `authenticated()`; only the offered tutor | Optional body `{note}` (trimmed, empty is none, at most 500: `422 decline_note_too_long`). `200` with the item. |

`AssignmentOfferItem`: `{id, scope, status, tutor {id, display_name},
offered_at, offered_by {id, display_name}, respond_by, responded_at,
decline_note, series, session_count, sessions [{id, state, starts_at,
ends_at, subject {name, education_level_name}, classroom {name} | null,
participants [{display_name}]}], sessions_truncated}`. `series` is `null`
for a session offer, else `{id, kind, cohort {id, name} | null, subject
{name, education_level_name}, weekdays, interval, start_time (HH:MM,
local), duration_minutes, time_zone, from_date, until, count,
last_occurrence_on}` (`cohort` names the course cohort of a `course`
series, M4-20, so the tutor sees the offer is for a course);
`session_count` is the number of covered sessions; a series offer lists
its first 10 by start (`sessions_truncated` when there are more).

**Offering an `unassigned` session** (body `{tutor_user_id, version,
respond_by?, override?}`, unknown fields `422`). Order: `401`/`403`
(`schedule.assign`) → `422` schema → `403 permission_denied` for an
`override` without `schedule.override` → the session `FOR UPDATE` (`404
not_found`) → `409 version_conflict` → `409 class_session_not_offerable`
(`details.state` unless `unassigned`; `details.reason = "started"` when
`starts_at <= now`) → the tutor lock (M4-18) → the zone `FOR SHARE` → `422`
for the tutor (`tutor_unknown`, `tutor_inactive`) and `respond_by` (`respond_by_invalid`)
in one response → the checks → the override decision (`409
scheduling_conflict` with every conflict unless none, or all soft and
listed) → the workload warnings → `unassigned → offered` (an `EXCLUDE` race
→ `409` with one `tutor_overlap`) → the override record, the offer, the
tutor's notification, and the session's open reassignment item is
cancelled → `201`. **Checked again:** `tutor_overlap` (hard),
`tutor_unavailable`, `tutor_not_qualified`, `center_closed`,
`outside_operating_hours`, `room_unavailable` (soft), with the create's
check functions. **Not re-checked:** `student_overlap`, `room_overlap`,
capacity (unchanged inputs, held by their writers' locks) and
`notice_window` (applied when the slot was booked; a late reassignment
stays possible without an override). Whether the tutor declined the
session before is not checked; teaching limits are warnings only.

**Answering** (the offered tutor only). Order: `401` → an unknown offer,
or one offered to someone else, is `404 not_found` (never `403`: staff,
other tutors, students and guardians all get `404`) → the lock order →
`409 offer_not_pending` (`details.status`) → `409 offer_expired` when
overdue → the change → `200`. **Accept:** every covered `offered` session
becomes `confirmed`; qualification and availability are not re-checked; no
notification. **Decline:** every covered `offered` session goes back to
`unassigned` (the tutor cleared; the room and students stay held), the
note is stored, and the reassignment item and the staff notifications
follow. A double answer, or a concurrent accept and decline, gives exactly
one `200`; the other is `409 offer_not_pending`.

**Expiry.** The periodic task `scheduling:expire_offers` (every five
minutes, `*/5 * * * *`, fixed `periodic_id` and `queueing_lock`
`scheduling-expire-offers`, ADR-0003) takes the `pending` offers with
`respond_by <= now`, oldest first, at most 200 per run, each in its own
transaction: under the lock order it re-checks that the offer is still
pending and overdue (else it skips it: a concurrent answer wins, and a
second run changes nothing), moves the still-`offered` sessions to
`unassigned`, marks the offer `expired`, and makes the reassignment item
and the `scheduling.offer_expired` notifications. A session no longer
`offered` (moved by another path) is left alone; the offer still expires,
and with nothing to reassign no item or notification is made. Audit events
of the task have no actor. It logs the counts (expired, skipped).

**Reassignment inbox item** (approval type `scheduling.offer_reassignment`,
review permission `schedule.assign`; no request-changes; dismissing needs a
reason). One per declined or expired offer (the subject is the offer),
requester `null`, priority `high` when the first covered session starts
within 24 hours, otherwise `normal`. Summary: `{class_session_id,
subject_name, starts_at, tutor_name, outcome (declined | expired),
decline_note, session_state, class_series_id, from_date, occurrence_count}`
(the last three `null` for a session offer; for a series offer that
covered no session, `class_session_id`, `starts_at` and `session_state` are
`null`). **Approve** ("Mark resolved") is `409
reassignment_still_needed` while a covered session is still `unassigned`
(for a series offer, also while the series has no tutor);
**Reject** ("Dismiss") closes it and leaves the session as it is. Offering
the session again cancels the open item of its latest declined or expired
**session** offer (resolved by the offering user); the items of series
offers are closed by offering the series again.

**Notifications** (in-app): `scheduling.offer_received` to the tutor
`{offer_id, subject_name, starts_at, respond_by}`;
`scheduling.offer_declined` / `scheduling.offer_expired` to every active
holder of `schedule.assign` (role grants plus delegations;
`identity.service.active_user_ids_with_permission`) `{offer_id,
class_session_id, subject_name, starts_at, tutor_name}`. Instants are RFC
3339 in UTC. The decline note is never a param. For a series offer the
same three types add `class_series_id`, `from_date` (local date) and
`occurrence_count` (the covered sessions at the time); `class_session_id`
of a declined or expired series offer is its first covered session, or
`null` when none was covered. A session offer's params don't carry these
keys.

**Withdraw seams.** `scheduling.service.withdraw_pending_offers(session,
class_session_ids, by_user_id=…)` marks the covering pending
**session-scope** offers `withdrawn` (no item, no notification) for the
later paths that cancel or substitute an `offered` session (M4-10, M4-13);
the caller holds the session locks and moves the sessions. Series offers
are left alone: `scheduling.service.withdraw_series_offer(session,
class_series_id, by_user_id=…)` (M4-09, M4-10) withdraws a series' pending
series offer from the series row on, returns its still-`offered` sessions
to `unassigned` and clears the series' tutor. No route.

#### Series offers

Implemented by M4-07b (#233; ADR-0005 D3) in `series_offers.py`,
`offer_flow.py` and `materialize.py`. One offer covers every
not-yet-started occurrence of a series from a local `from_date` onward.
The series row holds the assignment: `class_series.assignment_status`
(`pending` | `accepted`) and `assignment_from` (a local date), both set
exactly when the series has a tutor. **Occurrence state**
(`occurrence_initial_state(series, local_date)`): `unassigned` (no tutor)
without a tutor or before `assignment_from`; `offered` while the series
offer is pending (and attached to it, so the invariant holds); `confirmed`
once it is accepted, with no further offer. The offer's `respond_by` never
moves when later occurrences join it.

**Deadline:** as above, with the first covered session's start, or the
rule's next occurrence on or after `from_date` when no session is covered
yet (a series beyond the horizon gets an offer with no covered session).

**At creation.** `POST /api/v1/class-series` with a tutor makes the
pending series offer from `starts_on` in the same transaction over every
`offered` occurrence (`skipped_closure` ones are not covered) and notifies
the tutor (see Class series).

**Later** (`POST /api/v1/class-series/{class_series_id}/offers`, body
`{tutor_user_id, version, from_date?, respond_by?}`; `version` is the
series' version; unknown fields `422`, an `override` included: a series
offer never overrides, M4-09c adds that). Only for a series **without a
tutor** (created without one, or after a series decline or expiry).
Order: `401`/`403` → `422` schema → the series (`404 not_found`) → the
locks (the series' students, the zone `FOR SHARE`, the series row `FOR
UPDATE`) → `409 version_conflict` → `409 series_not_offerable`
(`details.reason`: `assigned` when it has a tutor, `ended` when
`from_date` is after `last_occurrence_on` or no occurrence from it is still
to come) → `422 from_date_in_past` (the default `from_date` is the later of
`starts_on` and the local today) → the **covered sessions**: the series'
`unassigned` sessions whose rule instant is on a local date on or after
`from_date` and that start after now, locked by id (`offered`,
`confirmed`, `conflict`, `skipped_closure` and `cancelled` ones are not
covered) → `422` for the tutor (`tutor_unknown`, `tutor_inactive`) and
`respond_by` (`respond_by_invalid`) in one response → the session-offer
checks on every covered session with the new tutor, `center_closed` off
(`tutor_overlap` hard; `tutor_unavailable`, `tutor_not_qualified`,
`outside_operating_hours`, `room_unavailable` soft; `notice_window`, the
student and room overlaps and capacity are not re-run) → any conflict is
`409 scheduling_conflict` listing every conflict of every covered session
(`details.occurrence_start`, `details.date`); nothing is written → every
covered session `unassigned → offered` (an `EXCLUDE` race → `409` with one
`tutor_overlap`), the series assignment (`pending` from `from_date`; its
version is bumped), the offer, the tutor's notification, and the open
reassignment items of the series' earlier declined or expired series
offers are cancelled → `201` with `warnings: []`. Offering one session of
a tutorless series through the session route stays possible; it changes
neither the series nor its items.

**Answering a series offer** (the order above; the series row is locked
before the sessions and the offer). **Accept:** every covered session still
`offered` becomes `confirmed` (others, cancelled meanwhile, are skipped);
the series becomes `accepted` (version bumped); later occurrences are
created `confirmed`. **Decline / expiry:** every covered session still
`offered` goes back to `unassigned`, the series loses its tutor and
assignment (version bumped; later occurrences are `unassigned`), and one
reassignment item plus the staff notifications are made, even when no
session was covered.

**Lock order** (series-scope writers): the series' students and the zone
`FOR SHARE` (only the paths that create rows or check slots: the series
offer route and materialization) → the series row `FOR UPDATE` → the
covered sessions by id → the offer. The answers, the expiry, the withdraw
seam, "Mark resolved" and the `scheduling.occurrence_conflict` approve take
the series row before the sessions, so they serialize with the nightly job
and can't deadlock.

**Kinds (#55 A1).** `assignment_offers.kind` is `assignment` (the default)
or `substitution` (always session scope, see Substitutions). Items carry
`kind` and `substitution {original_tutor {id, display_name}} | null` (the
substitute never sees the reason); `GET /api/v1/assignment-offers` and the
session's offer history keep their statement budgets. The invariant above
counts **assignment** offers only.

**Family visibility.** `scheduling.states.FAMILY_VISIBLE_STATES` =
`{confirmed, completed}` (`is_family_visible`): students and guardians see a
session only once a tutor accepted it; the family views and their record
policy are M4-22 (#67).

### Substitutions

Implemented by M4-10 (#55; spec §2 "Substitution", §3, W1.3–W1.4;
[ADR-0005 amendment A1](adr/0005-scheduling-invariants-overrides-and-recurrence.md#amendments))
in `apps/api/app/modules/scheduling/`: `substitutions.py` (the pure rules,
the invariants and the lock order), `substitution_flow.py` (substitute,
withdraw, list) and `substitution_records.py` (rows, items, the three
notification types); the substitute answers through the Assignment offers
routes.

**Model.** A substitution is an `assignment_offers` row of `kind =
'substitution'` (session scope) plus one `class_session_substitutions` row
(audited, not versioned, append-only): `class_session_id`, `offer_id`
(unique), `original_tutor_user_id` ≠ `substitute_tutor_user_id`, `reason`
(trimmed, 1–1000), `requested_by_user_id` and `slot_starts_at` /
`slot_ends_at` (the session's times when offered). Its status is the
offer's. **Invariant:** a session has at most one pending substitution,
only while it is `confirmed` with the original tutor at the snapshot's
times.

| Route | Access |
| --- | --- |
| `POST /api/v1/class-sessions/{class_session_id}/substitutions` | `schedule.assign` (an `override` needs `schedule.override`) |
| `GET /api/v1/class-sessions/{class_session_id}/substitutions` | `schedule.view` |
| `POST /api/v1/class-sessions/{class_session_id}/substitutions/{substitution_id}/withdraw` | `schedule.assign` |

**Create.** Body (`extra="forbid"`): `{substitute_tutor_user_id, version,
reason, respond_by?, override?}` (`version` is the session's; `override` is
the #49 member). Order: `401`/`403` → `422` (`reason_required`,
`reason_too_long`, the override's codes) → `403 permission_denied` for an
`override` without `schedule.override` → the session `FOR UPDATE` (`404`)
→ `409 version_conflict` → `409 class_session_not_substitutable` → the substitute's
`lock_tutor` (M4-18) → the zone `FOR SHARE` → `422` in one response:
`tutor_unknown`, `tutor_inactive`, `substitute_is_current_tutor` (on
`substitute_tutor_user_id`), `respond_by_invalid` (the offer deadline rule
with the session's start) → the offer checks for the substitute on the
session's own slot: `tutor_overlap` (hard), `tutor_unavailable`,
`tutor_not_qualified`, `outside_operating_hours`, `room_unavailable` and,
for a one-off session only, `center_closed` (soft); not `notice_window`,
the student or room overlaps or capacity → the override decision (`409
scheduling_conflict`, or the override row) → the offer, its session row and
the substitution row; **the session is not changed** → `scheduling.
offer_received` to the substitute (`kind`, `original_tutor_name`) → the
open reassignment items of the session's earlier substitutions are
cancelled → `201 {substitution, warnings}` (the substitute's workload
warnings). The substitute's slot is not held while the offer is pending.

**Answers.** The substitute accepts or declines with `POST
/api/v1/assignment-offers/{offer_id}/accept|decline` (anyone else: `404`).
Accept re-checks the session (`409 substitution_no_longer_valid`: no longer
`confirmed` with the original tutor at the snapshot's times; nothing is
written, the offer stays pending) and swaps the tutor (still `confirmed`,
version bumped; the substitute booked elsewhere meanwhile is `409
scheduling_conflict` with one `tutor_overlap`, nothing written);
`scheduling.tutor_substituted` goes to the original tutor and
`scheduling.session_tutor_changed` to the students' logins and guardians.
Qualification and availability are not re-checked. Decline and expiry leave
the session `confirmed` with its original tutor and raise the
`scheduling.offer_reassignment` item (its summary adds `kind` and
`original_tutor_name`; "Mark resolved" is always allowed) and the staff
notifications.

**Withdraw** (no body): `404` for an unknown session or a substitution of
another session → the session `FOR UPDATE`, then the offer → `409
offer_not_pending` (`details.status`) → `withdrawn`, `200` with the item. No
notification, no inbox item.

**List**: `{items}` newest first, each `{id, class_session_id, status,
offer_id, original_tutor, substitute_tutor, reason, requested_by,
requested_at, respond_by, responded_at, decline_note}`; at most four
statements.

**Lock order.** Substitute, withdraw and the answers take the session, then
the offer; the cancel takes the series row (for an occurrence), then the
session, then the offers. Neither takes a series row after a session row.

### Class series

Implemented by M4-08 (#53; spec §1, §3, §15 W1.2; ADR-0005 D4, D5, D6, D9;
ADR-0004) in `apps/api/app/modules/scheduling/` (`recurrence.py`: the pure
weekly rule; `series.py`: create and read; `materialize.py`: the rolling
materialization; `tasks.py`: the nightly job; `occurrence_approval.py`: the
inbox item; `series_edit.py`: editing and cancelling, M4-09). `POST
/api/v1/class-series` creates **individual** series only (`kind` other than
`individual` is `422`); a tutoring group's `group` series is created by
`POST /api/v1/groups` (M4-11, Tutoring groups below) and has `group_id` set
in `ClassSeriesDetail` (`null` otherwise). A series edit keeps a group
series' `group_id` on the series a split creates. A course cohort's
`course` series is created by `POST /api/v1/cohorts` (M4-20, Course cohorts
below) and has `cohort_id` set (`null` otherwise); a split keeps it too.
Series overrides: M4-09c (#236; `overrides.py`, `conflict_override.py`),
"Series overrides" below.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/class-series` | `require("schedule.manage")` (Owner, Administrator) | Body below. `201` with `ClassSeriesDetail` plus `warnings` (always `[]` for now). |
| `GET /api/v1/class-series/{class_series_id}` | `require("schedule.view")` (Owner, Administrator, Academic coordinator, Finance staff) | `ClassSeriesDetail`; `404 not_found` for an unknown id. Every holder sees every series. At most 9 statements. |
| `POST /api/v1/class-series/{class_series_id}/offers` | `require("schedule.assign")` | Offer a series without a tutor (see Assignment offers, "Series offers"). |
| `POST /api/v1/class-series/{class_series_id}/edits` | `require("schedule.manage")` (Owner, Administrator) | Change the rule from a date on (M4-09, #54); `200` with `ClassSeriesEdited`. See "Editing and cancelling". |
| `POST /api/v1/class-series/{class_series_id}/cancellations` | `require("schedule.manage")` | Cancel the occurrences from a date on (M4-09); `200` with `ClassSeriesCancelled`. See "Editing and cancelling". |

**Body** (unknown fields are `422`, `rrule` included: the API never takes
rule text; `override` is the series pairs member, "Series overrides"
below):

```json
{"kind": "individual", "student_id": "…", "subject_id": "…",
 "tutor_user_id": "…" | null, "classroom_id": "…" | null,
 "starts_on": "2026-11-09", "start_time": "16:00", "duration_minutes": 60,
 "recurrence": {"weekdays": [1, 3], "interval": 1, "until": "2026-12-18" | null, "count": null},
 "respond_by": "…" | null,
 "override": {"pairs": [{"occurrence_start": "2026-11-09T22:00:00Z", "code": "outside_operating_hours"}], "reason": "…"} | null}
```

- `respond_by` (M4-07b; only with a tutor, else `422
  respond_by_requires_tutor`): an earlier deadline for the series offer,
  checked after the references against the rule's first occurrence (`422
  respond_by_invalid`). With a tutor, a series with no occurrence still to
  come is `409 series_not_offerable` (`ended`).

- `starts_on` is the local date the rule starts on (the first occurrence is
  the first matching weekday on or after it); `start_time` the local
  wall-clock time in the center zone, `HH:MM` only (`422 time_format`
  otherwise, seconds included); `duration_minutes` 15–480.
- `recurrence`: `weekdays` ISO 1 (Monday) to 7 (Sunday); `interval` every
  1–4 weeks (default 1); `until` (an inclusive local date) **or** `count`
  (1–200 occurrences in all, the ones skipped for a closure included), or
  neither (open-ended).

The series stores the structured fields, the center zone (`time_zone`) and
the derived, normalized RFC 5545 rule
`FREQ=WEEKLY;INTERVAL=<n>;WKST=MO;BYDAY=<MO,…>` followed by
`;UNTIL=<YYYYMMDD>T235959` (floating local time) or `;COUNT=<n>`.
`last_occurrence_on` is the local date of a finite series' last
occurrence (`null` for an open-ended one).

**Order of the create:**

1. `401`/`403`, then `422` for the schema (the `override` codes included);
   then `403 permission_denied` when `override` is sent without
   `schedule.override`, before anything is read (M4-09c);
2. the per-student advisory lock (as Class sessions);
3. the center time zone, read `FOR SHARE`;
4. `422` for the recurrence, on `recurrence.weekdays` (`weekdays_empty`,
   `weekday_out_of_range`, `weekdays_duplicate`), `recurrence.interval`
   (`interval_out_of_range`), `recurrence.until` (`until_and_count`,
   `until_before_start`), `recurrence.count` (`count_out_of_range`) or
   `recurrence` (`no_occurrences`: a finite rule with no date);
5. `422 starts_on_in_past` when `starts_on` is before the center's local
   today;
6. `422 crosses_local_date` on `start_time` when `start_time` plus the
   duration is after 24:00 (ending exactly at midnight is allowed);
7. `422` for the references, every one in one response (the Class sessions
   codes);
8. every occurrence in the window `[now, now + SCHEDULING_HORIZON_WEEKS)`
   is expanded: one on a closed local date (an active closure) is planned
   `skipped_closure` with no checks; every other one gets every Class
   sessions check except `center_closed` (a closure date never gets that
   far). A bounds failure (only where a DST change falls at local midnight)
   is `422 crosses_local_date` on `start_time`;
9. any conflict: `409 scheduling_conflict` with **every** conflict of
   **every** occurrence, by occurrence then in the Class sessions order,
   each `details` with `occurrence_start` (the instant, UTC) and `date`
   (the local date). Nothing is created. With an `override`, the series
   override decision instead ("Series overrides"): a hard conflict or an
   unlisted soft pair is the same `409`;
10. the series, its participant and every occurrence (with its
    participant) are stored (an overridden occurrence in its clean state,
    without `conflict_codes`); with a tutor, the pending series offer over
    every `offered` occurrence and the tutor's notification (M4-07b); with
    something overridden, the override rows and the
    `scheduling.series_override` event (M4-09c); `201`.

A series that starts after the horizon is created with no occurrence: the
nightly job creates and checks them when they enter the window.

**DST and times.** The rule is expanded in local wall-clock time; each
local start is placed in the zone with `fold=0`: an ambiguous time (fall
back) takes its first instant, a nonexistent one (spring forward) moves
forward by the gap. The end is the start plus the duration in elapsed
minutes, so `scheduled_minutes` always equals `duration_minutes`.

**Occurrences.** Each is a class session (`kind` `individual`, the series'
subject, room, one participant, `series_id` and `original_start` set, and
the series' tutor when its assignment covers the date). A clean one takes
`scheduling.materialize.occurrence_initial_state` for its local date
(M4-07b): `offered` (attached to the pending series offer) while the
series offer is pending, `confirmed` once it is accepted, `unassigned`
without a tutor or before the assignment's from-date. A `skipped_closure` one keeps the tutor, room and
participant and holds nothing (no overlap, never billable, no makeup,
ADR-0005 D6). Audit: only the automatic `create` events of the series, its
participant and each session and session participant.

`ClassSeriesDetail` is `{id, kind, subject {…}, tutor {…} | null,
classroom {…} | null, participants [...], starts_on, start_time (HH:MM),
duration_minutes, time_zone, recurrence {weekdays, interval, until, count},
rrule, last_occurrence_on, ended, split_from_series_id,
split_into_series_ids, assignment, version, created_at, updated_at,
occurrences [{id, state, starts_at, ends_at, original_start,
is_exception, overridden, conflict_codes, version}]}` (`overridden`: at
least one override row names the session, read in the same statement;
`conflict_codes` and `version` as on the session, M4-09c). `last_occurrence_on` means "no occurrence after this
date"; `ended` (M4-09) is true when it is set and before the center's local
today, so the web never compares dates itself. `split_from_series_id` is
the series this one was split from, `split_into_series_ids` the series
split from it (oldest first). `assignment` (M4-07b) is `null` without a tutor, else
`{status (pending | accepted), tutor {id, display_name}, from_date,
offer_id, respond_by}` (`respond_by` only while `pending`); the
occurrences are the series' rows starting now or later, by start. The web
app never parses `rrule`.

**Horizon and the nightly job.** `SCHEDULING_HORIZON_WEEKS` (an operator
setting, default 12, 4–26; API and worker) sets how far ahead occurrences
exist. The periodic task `scheduling:materialize_series` runs every day at
08:00 UTC (`0 8 * * *`; Procrastinate evaluates the cron in UTC) with a
queueing lock, in every environment. It extends every series that is
open-ended or whose last occurrence is today or later, one transaction per
series (the system is the actor; a failing series is logged with its id and
error class, and retried next run), and logs one line with counts only. It
can be run by hand: `scheduling.tasks.run_materialization(settings)`. Per
series it takes the student locks, the zone `FOR SHARE` and the series row
`FOR UPDATE` (in that order), leaves out the `original_start` values that
already exist, and inserts the rest through the ORM (audited). `INSERT … ON
CONFLICT DO NOTHING` is not used: the audit guard refuses Core inserts into
audited tables; `UNIQUE (series_id, original_start)` is the backstop. A
rerun inserts nothing, and the extension never updates an existing row
(any state); closure changes are reconciled just before it (below).

In the job, an occurrence on a closed date is `skipped_closure`; one that
fails a check, or whose references became invalid since the create
(`student_inactive`, `subject_inactive`, `tutor_inactive`,
`classroom_inactive`, or an `…_unknown`), is stored as **`conflict`**:
`conflict_codes` holds the codes, it keeps the tutor, room and participant
and holds nothing. Each `conflict` row raises one **`scheduling.occurrence_conflict`**
approval request (subject: the session; requester `null`, the system;
reviewers hold `schedule.manage`; no request-changes; a rejection needs a
reason). Its summary is `{class_session_id, series_id, starts_at,
subject_name, student_display_names, tutor_display_name, conflict_codes}`.

- **Approve = re-check and schedule:** the student locks, the zone `FOR
  SHARE`, the series row `FOR UPDATE` (M4-07b), the session `FOR UPDATE`.
  A session no longer in `conflict` is `409 approval_transition_invalid`;
  invalid references are `422` (the create's codes); the checks run again
  now with the tutor the series assigns on that date (`center_closed`
  off): still failing is `409 scheduling_conflict` and the request stays
  `pending`; clean moves the session to the occurrence state of its date
  (`offered` and attached to the pending series offer, `confirmed`, or
  `unassigned`). A start already in the past fails `notice_window`: reject
  it instead.
- **Reject = cancel the occurrence** with the reviewer's reason.

Approving never overrides; a `conflict` row's soft conflicts are overridden
with `POST /api/v1/class-sessions/{id}/conflict-overrides` ("Series
overrides" below). The job never reads earlier overrides.

**Closure changes on existing occurrences** (M4-08b, #232; ADR-0005 D6;
`scheduling/closure_changes.py`). For each series, in the same transaction
and right before the extension, the job reconciles the series' **future**
rows (`starts_at` after the run's `now`) with the active closures, by the
local date each row is held on. Same lock order, then the rows `FOR
UPDATE`; one `closures_between` call.

- **A date that is now closed** (a closure added, extended or reactivated):
  `unassigned`, `offered` and `confirmed` rows (exceptions included) become
  `skipped_closure`. They keep their tutor, room and participants and hold
  nothing. `completed`, `cancelled`, `conflict` (its inbox item still
  decides it) and `skipped_closure` rows, past rows and one-off sessions
  are never changed. A pending session-scope offer of a skipped `offered`
  row is withdrawn; a pending **series** offer stays (the row keeps its
  covered-session row; an accept or decline only moves rows still
  `offered`).
- **Notification `scheduling.occurrence_skipped_closure`** `{class_session_id,
  series_id, starts_at}` (no closure name), one per recipient per skipped
  row, decided by the state before the skip: the row's tutor when it was
  `offered` or `confirmed`; for `confirmed` only, also the student's own
  login and the accounts of the student's linked guardians. No link.
- **A date that is no longer closed** (a closure deactivated or shrunk): a
  `skipped_closure` row on it runs the job's checks again (references,
  bounds, every Class sessions check with `now`, `center_closed` off) with
  the tutor the series assigns on its date. Clean: the occurrence state of
  its date (`unassigned`; `offered`, attached to the pending series offer
  if not already covered; or `confirmed`). Failing: `conflict` with its
  `conflict_codes` and one `scheduling.occurrence_conflict` request, as
  above. A row that was a conflict before keeps its one request: a decided
  (or cancelled) request goes back to `pending`, with its reason and
  resolver cleared, and is decided again like a new one. Only a row whose
  rule date the series' current rule still produces, on or before its
  `last_occurrence_on`, is restored (M4-09): a skipped row an edit or a
  cancellation left on a dropped date or after the series' end stays
  `skipped_closure`. A tutor or room overlap that appears between the checks and the
  write makes that row `conflict` too; the rest of the series still goes
  through. A restore notifies nobody.
- **Idempotent:** a rerun changes nothing and writes no event, notification
  or request. The job's log line adds `closure_skipped_count`,
  `closure_restored_count` and `closure_conflict_count`.
- **When:** every night with the extension, and within minutes of a closure
  write: `POST /api/v1/closures`, and a `PATCH` that changes the dates or
  `is_active`, queue the job after they commit (see Academic periods and
  closures).

#### Editing and cancelling

M4-09 (#54; spec §3, W1.5; ADR-0005 D4; `scheduling/series_edit.py`). One
occurrence is moved with `PATCH /api/v1/class-sessions/{id}` (Class
sessions, "Moving a session"); it becomes an exception. A series changes
from a date on:

```json
POST /api/v1/class-series/{id}/edits
{"mode": "following" | "all", "from_date": "2026-12-07" | null, "version": 3,
 "start_time": "17:00", "duration_minutes": 60, "classroom_id": "…" | null,
 "recurrence": {"weekdays": [2, 4], "interval": 1, "until": null, "count": null}}

POST /api/v1/class-series/{id}/cancellations
{"mode": "following" | "all", "from_date": "2026-12-07" | null,
 "reason": "…", "version": 3}
```

The edit body is the **full** new rule (not a patch), with the create's
field rules, plus the optional `override` pairs member (M4-09c; the pairs
name the **new** instants); unknown fields are `422`, and `override` on a
cancellation is `422` (it runs no checks). An edit changes the time, the duration, the room and the weekly
rule only; the tutor changes through offers and substitutions, a student or
subject change is a new series, and cancelling one occurrence is M4-10.
The cancellation `reason` is trimmed, 1–1000 characters (`reason_required`,
`reason_too_long`).

**The date `D`.** `following` starts on `from_date` (a local date);
`all` (with `from_date = null`) starts on the local date of the series'
earliest occurrence that hasn't begun (`starts_on`, or today, when there is
none).

**Order** (both routes): `401`/`403`, `422` for the schema; for an edit
with an `override`, `403 permission_denied` without `schedule.override`
(before the `404`: nothing is read); `404`; the
student locks, the zone `FOR SHARE`, the series row `FOR UPDATE`, then its
rows from `D` `FOR UPDATE` by id, then the offers (the nightly job's order:
they never deadlock); `409 version_conflict` (the series `version`); for an
edit only, `409 series_offer_pending` while its tutor offer waits for an
answer; `422 from_date_in_past`; `409 series_ended` when `D` is after
`last_occurrence_on`; `409 occurrence_started` when an occurrence on `D`
has begun. Then, for an edit: the recurrence `422`s (the create's codes;
`until_before_start` is against `D`), `422 crosses_local_date` on
`start_time`, the references `422` (the series' students, subject and tutor
and the new room), `422 series_unchanged` (same rule and no edited
occurrence from `D`), the checks below, the write, `200`.

**Never rewritten.** An occurrence has *begun* when `starts_at <= now`.
Past, begun, `completed` and `cancelled` occurrences are never changed by
an edit or a cancellation (a test compares them column by column); the one
exception is the re-point below.

**In place or split.** When the series has no occurrence row before `D`,
the series itself takes the new rule (`starts_on = D`, version bumped; no
lineage). Otherwise it is **split**: the old series ends with `until = D −
1` (a `COUNT` series becomes an `UNTIL` one; `last_occurrence_on` is its
last date before `D`) and a **new series** starts on `D` with the same
kind, subject, students, zone and tutor assignment (from the later of its
from-date and `D`), the new rule and `split_from_series_id`. The web
pre-fills the new rule's end as the old `last_occurrence_on`, so the end
stays the same.

**Occurrences from `D`** (the ones that haven't begun), matched to the new
rule's dates in `[now, now + SCHEDULING_HORIZON_WEEKS)` by **local date**:

| Occurrence | Result |
| ---------- | ------ |
| on a kept date, `unassigned` / `offered` / `confirmed` / `conflict` | **moved**: same id, the new series, rule instant, times and room; tutor, students and state kept (a `conflict` one that passes takes the occurrence state, its request cancelled); an edited one loses its exception (`exceptions_reset`); on a closed date it becomes `skipped_closure` |
| on a kept date, `skipped_closure` | moved with the new times; the nightly reconciliation restores it if its date is open |
| on a kept date, `cancelled` | **re-pointed**: only `series_id` and `original_start` change, so the date doesn't come back |
| on a dropped date, `unassigned` / `offered` / `confirmed` / `conflict` | `cancelled`, reason `series_changed` |
| on a dropped date, `skipped_closure` / `cancelled` | untouched (left on the old series) |
| a new date | created as the nightly job would (`skipped_closure` on a closed date) |

**Checks.** Every created occurrence and every moved one whose times or
room change (or that is in `conflict`) gets every Class sessions check
except `center_closed` (a closed date gives `skipped_closure`), with its own
tutor; the occurrences the edit moves, re-points or cancels are left out of
the overlaps, so an edit never conflicts with itself. Any conflict refuses
the whole edit: `409 scheduling_conflict` in the create's per-occurrence
format (`details.occurrence_start` is the **new** instant) and nothing is
written, unless an `override` covers them ("Series overrides"). `warnings`
is always `[]`.

**`ClassSeriesEdited`** is `{series: ClassSeriesDetail (the new one after a
split), previous_series_id | null, split, summary {moved, created,
cancelled, skipped_closure, exceptions_reset}, warnings: []}`.

**A cancellation** cancels every occurrence from `D` in `unassigned`,
`offered`, `confirmed` or `conflict` with the reason; `skipped_closure` and
`cancelled` ones are kept. A pending tutor offer of the series is withdrawn
(its offered rows go back to `unassigned`). The series ends with `until =
last_occurrence_on = D − 1` (which can be before `starts_on` when nothing
was held), so the nightly job creates nothing more; a repeated cancellation
is `409 series_ended`. **`ClassSeriesCancelled`** is `{series, summary
{cancelled, skipped_closure_kept}}`.

**Side effects**, in the same transaction:

- a row that leaves `conflict` (moved or cancelled) has its open
  `scheduling.occurrence_conflict` request cancelled by the system;
- a cancelled (or skipped) `offered` row's pending offer is `withdrawn`; a
  moved one keeps its offer, with `respond_by` capped at its first covered
  start;
- notifications `scheduling.series_changed` / `scheduling.series_cancelled`
  (see Notifications): the tutor of the `offered` or `confirmed` rows
  changed or cancelled and, for `confirmed` rows, the student's login and
  its linked guardians; one per recipient per write, never the actor;
- audit: the automatic events plus one named event on the series,
  `scheduling.series_changed` `{series_id, new_series_id | null, mode,
  from_date, moved, created, cancelled, exceptions_reset}` or
  `scheduling.series_cancelled` `{series_id, from_date, cancelled}`.

**The nightly job** extends the new series of a split (its pre-filter sees
the moved and re-pointed rows, so no date is held twice) and stops selecting
the old one once its `last_occurrence_on` has passed. It re-reads the series
row under its lock, so it always sees the rule an edit committed.

#### Series overrides

M4-09c (#236; spec §3, W1.6; ADR-0005 D1, D4 "soft conflicts can be
overridden for listed occurrence and code pairs", "Conflict wire format and
overrides"; `scheduling/overrides.py`). The holders of `schedule.override`
are the one-off sessions' (the Owner by role, an Administrator only while an
Owner's delegation is active; checked on the effective permissions per
request). `POST /api/v1/class-series` and `…/edits` take:

```json
"override": {"pairs": [{"occurrence_start": "2026-11-15T16:00:00Z", "code": "outside_operating_hours"}, …],
             "reason": "…"}
```

- `422` codes: `pairs_required` (missing or empty), `pairs_too_many` (more
  than 1400: 7 soft codes × 200 occurrences) and `duplicate_pair` (the same
  instant, compared in UTC, and code twice) on `override.pairs`;
  `code_not_overridable` (a hard code) / `code_unknown` (a warning code or
  any other string) on `override.pairs[i].code`; `occurrence_start_invalid`
  (naive, or not a whole minute) on `override.pairs[i].occurrence_start`;
  the reason as for a session (`reason_required`, `reason_too_long`;
  trimmed, 1–1000). A `codes` member is `422 extra_forbidden`.
- `403 permission_denied` when `override` is sent without
  `schedule.override`, right after the schema and before anything is read
  (also before an edit's `404`, and even when nothing would conflict).
- **The decision** (`evaluate_series_override`, pure): the conflicts are
  recomputed inside the write's transaction, under its locks, and grouped by
  `details.occurrence_start` (instants compared as UTC values, never as
  text). For each occurrence, the one-off rule (`evaluate_override`) runs
  with the codes paired with that instant: **any hard conflict on any
  occurrence**, or **any soft conflict whose (occurrence, code) pair isn't
  listed**, refuses the whole write with the complete per-occurrence `409`
  (it doesn't echo the override) and nothing is written. Listed pairs that
  don't occur (an occurrence out of the window or no longer conflicting, an
  instant that matches no occurrence, a `skipped_closure` date) are ignored
  and never stored. With no conflict at all the write goes ahead as without
  an override: no row, no event. A conflict that appeared after the first
  `409` (an occurrence that entered the horizon, say) is answered with a
  `409` again.
- **Edits:** pairs name the occurrence's **new** instant. An edit needs its
  own override: an occurrence overridden earlier that the edit re-checks
  must be listed again if its soft conflict still occurs; its older rows
  stay as history and are never consulted. `series_unchanged` stays `422`
  with an override.
- **Rows:** one append-only `class_session_overrides` row per overridden
  occurrence (its own `class_session_id`, the computed `codes` and
  `conflicts`, the reason, the actor) with the **context** column
  `class_series_id`: the series the write targeted (the new series after a
  split). All rows of a write are inserted with one flush.
- **Audit:** one named event per write, `scheduling.series_override` on the
  series (`record_type` `class_series`), `details {write: create | edit,
  occurrence_count, occurrences [{override_id, class_session_id,
  occurrence_start, codes}], reason}`; the full conflicts stay on the rows.
  The automatic events and an edit's `scheduling.series_changed` are
  unchanged. No notification about the override.
- **No carry-forward.** The nightly job and the closure restore never read
  override rows: an occurrence the job creates later that fails a soft check
  is a `conflict` row with an inbox item, whatever earlier writes
  overrode. Each override is a fresh authorization against conflicts
  recomputed at that moment (a qualification revoked later must not stay
  overridden). The inbox approve still only re-checks.

**Overriding one `conflict` occurrence:** `POST
/api/v1/class-sessions/{class_session_id}/conflict-overrides`
(`require("schedule.manage")` plus `schedule.override`;
`scheduling/conflict_override.py`), body `{version, override: {codes,
reason}}` (the one-off session member, required; unknown fields `422`).
Order: `401`/`403`; `422` for the schema; `404` for an unknown id (before
the permission, so an Administrator without the delegation sees `404` for
an unknown id); `403 permission_denied` without `schedule.override`; the
student locks, the zone `FOR SHARE`, the series `FOR SHARE`, the session
`FOR UPDATE`; `409 version_conflict`; `409 session_not_in_conflict`
(`details.state`) unless it is `conflict`; `409 session_not_editable`
(`details.reason = started`) when it has begun; `422` for its references
with the create's codes (`tutor_inactive`, …: data validity is never
overridable) and its bounds; the checks with `now` (`center_closed` off)
and the series' tutor for its date; the one-off override decision (`409
scheduling_conflict` with the single-session body, no
`occurrence_start`); the approve's transition (the occurrence state of its
date; an `offered` row joins the pending series offer; `conflict_codes`
kept); with something overridden, the override row (with
`class_series_id`) and `scheduling.session_override`; its open
`scheduling.occurrence_conflict` request is cancelled by the system (also
when nothing conflicts any more, which records no override); `200` with
`ClassSessionDetail`.

### Tutoring groups

Implemented by M4-11 (#56; spec §1, §3, §6, §15 W1.5; ADR-0005 D1, D2, D7,
D9; architecture §4.2, ISSUE-J1, ISSUE-SC3) in
`apps/api/app/modules/scheduling/` (`groups.py`: create, rename, enroll,
withdraw; `groups_rules.py`: the pure rules; `group_reads.py`: the reads
and the student record policy; `group_lock.py`: the group lock) and
`apps/api/app/workflows/` (`group_enrollment.py`: the billing hook;
`groups_router.py`: the routes, next to the workflow they call). The web
pages are M4-11b.

A group has a `name` (trimmed, 1–120, unique ignoring case), a subject
(its level follows) and `max_students` (1–500). Its tutor, room and weekly
rule live on its `group` series; its sessions are that series' occurrences
(`kind = "group"`, `group_id` set), materialized like any series, with every
enrolled student as a participant.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/groups` | `require("schedule.manage")` (Owner, Administrator); an `override` also needs `schedule.override` | Body below. `201` with `GroupDetail` plus `warnings` (`[]`). |
| `PATCH /api/v1/groups/{group_id}` | `require("schedule.manage")` | `{version, name?, max_students?}`; `200` with `GroupDetail`. |
| `POST /api/v1/groups/{group_id}/enrollments` | `require("schedule.manage")`; an `override` also needs `schedule.override` | `{student_id, override?: {codes, reason}}`; `201` with `GroupEnrollmentItem` plus `warnings` (`[]`). |
| `POST /api/v1/groups/{group_id}/enrollments/{enrollment_id}/withdraw` | `require("schedule.manage")` | `{version, reason?}`; `200` with `GroupEnrollmentItem`. |
| `GET /api/v1/groups` | `authenticated()` + record policy | `?q` (name contains, ignoring case), `?subject_id`, `?status=active\|ended\|all` (default `active`: a series still producing sessions), cursor pagination (`limit` 1–100, default 25; by name, then id). |
| `GET /api/v1/groups/{group_id}` | `authenticated()` + record policy | `GroupDetail`; hidden or unknown is `404 group_not_found`. At most 8 statements. |
| `GET /api/v1/groups/{group_id}/sessions?from=…&to=…` | `authenticated()` + record policy | The group's sessions overlapping `[from, to)` by start, the calendar's range rules (`422 range_invalid`, `range_too_long` over 45 days). At most 6 statements. |

**Create body** (unknown fields are `422`, `rrule` included):

```json
{"name": "Álgebra sábado", "subject_id": "…", "max_students": 6,
 "tutor_user_id": "…" | null, "classroom_id": "…" | null,
 "starts_on": "2026-11-09", "start_time": "10:00", "duration_minutes": 90,
 "recurrence": {"weekdays": [6], "interval": 1, "until": null, "count": null},
 "student_ids": ["…"],
 "override": {"codes": ["group_capacity_exceeded"],
              "pairs": [{"occurrence_start": "…", "code": "outside_operating_hours"}],
              "reason": "…"} | null}
```

`student_ids` are the initial students (0–500, each once:
`student_ids_duplicate`). The timing and `recurrence` follow `POST
/api/v1/class-series`. Without a tutor the occurrences are `unassigned`;
with one they are `offered` under a series offer (Assignment offers,
"Series offers"). The group `override` has `codes` for the group-level
conflict (`group_capacity_exceeded`, the #49 shape) and `pairs` for the
occurrences' soft conflicts (the series shape); at least one of them
(`codes_required`), and the reason as for every override.

**Order of the create:** `401`/`403` → `422` schema (name `name_required` /
`name_too_long`, `max_students` 1–500) → `403` for an `override` without
`schedule.override` → the student locks → the zone `FOR SHARE` → `422`
recurrence, `starts_on_in_past`, `crosses_local_date` → `422` references
(the students with their index, `["body", "student_ids", i]`; subject,
tutor, room), all in one response → `409 group_name_taken` → the conflicts:
the group-level `group_capacity_exceeded` (more initial students than
`max_students`) first, then every conflict of every occurrence in the
horizon (with `details.occurrence_start` and `details.date`), including
`room_capacity_exceeded` (hard) and `student_overlap` → `409
scheduling_conflict` unless the override covers every soft one (a hard one
never) → the group, its series, the series participants, one enrollment per
student and the occurrences → the overrides: per occurrence as for a series
(`scheduling.series_override`), and for the group level one row per
enrollment above `max_students` (in body order) with the event
`scheduling.enrollment_override`.

**Capacity** (ADR-0005 D7, ISSUE-SC3): the effective capacity is
`min(max_students, the current series' room capacity)` (`max_students`
without a room). The group maximum counts **active enrollments** and is
soft (`group_capacity_exceeded`, overridable); a room counts each future
occurrence's participants and is hard (`room_capacity_exceeded`), because
one occurrence may use another room. The nightly job never re-checks the
maximum; a room it can't hold becomes a `conflict` occurrence with an
inbox item. A `PATCH` can't lower `max_students` below the active
enrollments (`422 max_students_below_enrolled`); raising it above the room
is allowed.

**Enroll** (`schedule.manage`): `422` schema → `403` override without
`schedule.override` → the student lock → the zone `FOR SHARE` → the group
`FOR UPDATE` (`404 group_not_found`) → the group's producing series `FOR
UPDATE` (none: `409 group_ended`) → `422 student_unknown` /
`student_inactive` → `409 already_enrolled` → the conflicts, all listed:
`group_capacity_exceeded` (active enrollments + 1 over the maximum; soft)
first, then each future occupying occurrence's `student_overlap` and
`room_capacity_exceeded` (hard), with `details.occurrence_start` and
`details.date`, by start → `409 scheduling_conflict` unless an `override`
(`{codes, reason}`) lists every soft one → the enrollment, the series
participants and the occurrences' participants → with an override, one
override row whose subject is the enrollment and the event
`scheduling.enrollment_override` (`override_id`, `group_enrollment_id`,
`codes`, `conflicts`, `reason`).

**Withdraw** (`schedule.manage`): `422` (reason trimmed, 1–1000 when sent:
`reason_required` for a blank one, `reason_too_long`) → the student lock →
the zone → the group `FOR UPDATE` (`404 group_not_found`) → the producing
series → `404 enrollment_not_found` (unknown, or of another group) → `409
version_conflict` → `409 enrollment_not_active`. A withdrawal never fails a
check and changes no session state (an empty occurrence stays; cancel it
with `POST /api/v1/class-sessions/{id}/cancel` if needed).

**Mid-term rule:** an enrollment or a withdrawal takes effect at the request
instant. It changes the group's occurrences that start **after** now in
`unassigned`, `offered`, `confirmed`, `conflict` or `skipped_closure`, and
the participants of every producing series (so later materializations
follow). Past, started, `completed` and `cancelled` occurrences keep their
participants. Only `unassigned`, `offered` and `confirmed` occurrences are
checked; `conflict` and `skipped_closure` ones take the change without
checks (their approve and restore re-check every participant). Every change
bumps the `version` of the series and occurrences whose participants it
changes.

**Lock order** (every writer that changes a group occurrence's
participants or room): students → zone → **group `FOR UPDATE`** → series →
sessions → offers. The nightly job takes it too and, for a group series,
re-reads the participants under the locks; when an enrollment committed in
between it rolls that series back and retries (at most 3 times, then the
next night). A series edit or `PATCH` of a group occurrence takes the group
lock too; an approve of a `conflict` occurrence whose participants changed
since it read them is `409 version_conflict`.

**`GroupDetail`:** `{id, name, subject {id, name, level {id, name}},
max_students, enrolled_count, effective_capacity, current_series {id, tutor,
classroom, starts_on, start_time, duration_minutes, time_zone, recurrence,
last_occurrence_on, version} | null, next_occurrences [{id, state,
starts_at, ends_at}] (up to 10), version, created_at, updated_at, roster}`.
`current_series` is the producing series with the latest start (`null`
once the group has ended). `roster` (`schedule.view` holders only) lists
the active enrollments by student name, then the withdrawn ones, latest
first. **`GroupEnrollmentItem`:** `{id, student {id, display_name,
is_active}, enrolled_at, enrolled_by {id, display_name}, withdrawn_at,
withdrawn_by, withdrawal_reason, overridden, version}`.

**Who sees what:** `schedule.view` holders (Owner, Administrator, Academic
coordinator, Finance) see every group, its roster and every session. A
**student** sees only the groups where their own profile has an active
enrollment, with `enrolled_count` and `roster` `null`, and only the
sessions where their profile is a participant in `confirmed` or
`completed` (so nothing before their enrollment or after their withdrawal;
`next_occurrences` likewise). Anyone else (tutors, guardians, a student
account without a profile) gets an empty list and `404 group_not_found`.
M4-22 extends this to linked guardians and assigned tutors.

**The billing hook (M6-07):** `group_enrollments` keeps one row per
enrollment period (`enrolled_at`/`_by`, then a write-once
`withdrawn_at`/`_by`/`withdrawal_reason`; a trigger refuses deletes and
any other change, SQLSTATE 42501). The routes call
`app/workflows/group_enrollment.py`, whose `apply_enrollment_change`
step receives each `GroupEnrollmentChange {enrollment_id, group_id,
student_id, change: enrolled | withdrawn, effective_at, actor_user_id}` in
the same Unit of Work; M6-07 adds its charge step there, and its sweeper
reads `scheduling.service.list_group_enrollment_periods(changed_since,
limit)`. No job is enqueued and there is no event bus.

### Course cohorts

M4-20 (#65; spec §3 "each cohort has its own dates, tutors, enrolled
students, pricing and attendance"; ADR-0005 D1-D4, D6, D7, D9). A cohort is
one run of a course template. The cohort row (`course_cohorts`: a name
unique ignoring case, the template **snapshot**, one subject, a maximum of
1-500) lives in the `courses` module; its sessions are the occurrences of
one weekly `course` series in `scheduling` (`class_series.cohort_id`, copied
to every occurrence), which keeps the tutor, the room and the timing. The
routes live in `app/workflows/cohorts_router.py` and call
`app/workflows/cohorts.py`, which composes `courses.service` and
`scheduling.service` in one Unit of Work; `courses` never imports
`scheduling`. Enrollment is M4-21 (#66): a cohort created here has no
students. The web pages are M4-20b (#243).

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/cohorts` | `require("schedule.manage")` (Owner, Administrator); an `override` also needs `schedule.override` | Body below; `201` with `CohortDetail` plus `warnings` (`[]`). |
| `PATCH /api/v1/cohorts/{cohort_id}` | `require("schedule.manage")` | `{version, name?, max_students?}` (at least one: `422 no_changes`); `200` with `CohortDetail`. |
| `GET /api/v1/cohorts` | `require("course.view")` (Owner, Administrator, Academic Coordinator, Finance Staff) | `?q` (the name contains it, ignoring case; at most 120), `?template_id`, `?subject_id`, `?status=upcoming\|in_progress\|ended\|current\|all` (default `current`: upcoming and in progress); the #21 offset page `{items, total, limit, offset}` (`limit` 1-100, default 25) by name, then id. At most 5 statements whatever the page size. |
| `GET /api/v1/cohorts/{cohort_id}` | `require("course.view")` | `CohortDetail`; unknown is `404 cohort_not_found`. At most 8 statements. |
| `GET /api/v1/cohorts/{cohort_id}/sessions?from=…&to=…` | `require("course.view")` | The cohort's sessions overlapping `[from, to)` by start, in every state, with the calendar's range rules (`422 range_invalid`, `range_too_long` over 45 days); unknown is `404 cohort_not_found`. At most 6 statements. |
| `POST /api/v1/class-series/{class_series_id}/offers` | `require("schedule.assign")` (Assignment offers, unchanged) | Also offers a cohort's series to a tutor, under the cohort lock. |

In M4-20 every cohort read is staff only (Tutor, Student and Guardian get
`403`); M4-21 opens them to enrolled students and their guardians (a
record policy), M4-22 to tutors. Tutors already see their offers and
sessions.

**The create body** (unknown fields `422`): `{name, template_id,
subject_id, max_students, tutor_user_id?, respond_by?, classroom_id?,
starts_on, start_time (HH:MM), recurrence {weekdays, interval}, override?}`.
There is no `duration_minutes` (it is the snapshot's `session_minutes`), no
`recurrence.until` or `recurrence.count` (the count is derived, below), no
`rrule` and no `student_ids`. `respond_by` follows the series create
(`respond_by_requires_tutor`, `respond_by_invalid`); `override` is the
series `override` (`{pairs, reason}`, M4-09c).

**Order of the create:**

1. `401`/`403` (Coordinator, Finance, Tutor, Student, Guardian: `403
   permission_denied`), `422` for the schema, then `403` for an `override`
   without `schedule.override`;
2. the center zone `FOR SHARE` (no student lock: there are no students);
3. the template snapshot (`courses.service.snapshot_course_template`): the
   template `FOR SHARE` and the snapshot of its current version, reused or
   inserted; `422 template_unknown` / `template_inactive` on `template_id`;
4. every remaining `422` in one response: `subject_not_in_template`,
   `subject_inactive`, the recurrence (`weekdays_empty`,
   `weekday_out_of_range`, `weekdays_duplicate`, `interval_out_of_range`),
   `starts_on_in_past`, `crosses_local_date` on `start_time` (`start_time`
   plus the session length after 24:00), `respond_by_requires_tutor`, the
   tutor (`tutor_unknown`, `tutor_inactive`) and the classroom
   (`classroom_unknown`, `classroom_inactive`);
5. `409 cohort_name_taken`;
6. the derived `COUNT` (below; `422 too_many_occurrences`);
7. the cohort row, then the `course` series and its occurrences in the
   horizon, as a series create (Class series): closure dates are
   `skipped_closure`, every other occurrence passes the #48 checks
   (`tutor_not_qualified` against the cohort's subject), and any conflict
   the `override` doesn't cover is `409 scheduling_conflict` per
   occurrence (`details.occurrence_start`, `details.date`). **Nothing is
   written** on a `409` or `422`: the cohort and a new snapshot roll back
   with the series;
8. with a tutor, the pending series offer over the `offered` rows and the
   tutor's notification (Assignment offers, "Series offers"); without one
   the rows are `unassigned`.

Audit: the automatic `create` events of the cohort, a new snapshot, the
series, its sessions and the offer, plus `scheduling.series_override` when
something was overridden. A `403`/`409`/`422` writes nothing.

**The snapshot (the template is frozen for the cohort).** The cohort keeps
`template_snapshot_id` forever: editing, reordering or deactivating the
template later changes neither the cohort's snapshot nor its syllabus. A
second cohort on the same template version reuses the same snapshot; a
cohort created after an edit gets the new one. Re-snapshotting an existing
cohort is not supported. An inactive template can't start a cohort;
deactivating it later doesn't affect existing ones.

**The dates: a derived `COUNT` (E3).** The series has the template's
`session_count` sessions **outside the closures known at creation** (the
center's calendar is known when a course is sold, ADR-0005 D6): the server
expands the rule to its first 200 dates, reads the closures over them in
one `closures_between` call, and takes the smallest `COUNT` whose dates
hold exactly `session_count` open ones (`422 too_many_occurrences` when 200
are not enough, D4). A closure added **later** skips its session (no
automatic makeup, D6); a closure removed later restores it (#232). The
12-week horizon and the nightly job are unchanged.

**One subject (E4).** Every session of a cohort has the cohort's
`subject_id`, one of the snapshot's subjects; the tutor's qualification is
checked against it. Several subjects per cohort or per session are not
supported.

**The tutor (E5)** is assigned per cohort through the series offer: give
`tutor_user_id` on create (a pending series offer in the same
transaction), or later `POST /api/v1/class-series/{id}/offers` on the
cohort's series. Accepting, declining, expiry and later occurrences behave
as for an individual series. One tutor per cohort series; changing the
tutor of one session is a substitution (M4-10). The dates, the room and the
tutor change through the series (Class series, "Editing and cancelling";
Substitutions), never through `PATCH /api/v1/cohorts/{id}`.

**`CohortDetail`:** `{id, name, version, created_at, updated_at, template
{id, name, version} (as the snapshot froze them), template_snapshot_id,
subject {id, name, level {id, name}}, max_students, effective_capacity,
status, planned_session_count, session_minutes, first_session_on,
last_session_on, series, next_occurrences, syllabus}`.
`effective_capacity` is `min(max_students, the current series' room
capacity)`, or `max_students` without a room (ADR-0005 D7: raising
`max_students` above the room keeps the room's limit). `status` is
`upcoming` before the first session starts, `ended` once the last one has
ended, `in_progress` in between; it and `first_session_on` /
`last_session_on` come from the series rule, so they hold beyond the
horizon. `planned_session_count` and `session_minutes` are the snapshot's.
`series` is the current series (the latest `starts_on`) with the
`ClassSeriesDetail` fields but its occurrences (`assignment` and
`cohort_id` included). `next_occurrences` are up to 10 upcoming `{id,
state, starts_at, ends_at}`. `syllabus {modules, assessments, completion}`
comes from the snapshot, never from the live template.

**List items:** `{id, name, template {id, name}, subject, max_students,
tutor {id, display_name} | null, assignment_status | null,
first_session_on, last_session_on, next_starts_at | null, status}`.

**Session items:** `{id, state, starts_at, ends_at, original_start,
is_exception, tutor {id, display_name} | null, classroom {id, name} |
null}`.

**`PATCH`:** `403` → `422` → the cohort `FOR UPDATE` → `404
cohort_not_found` → `409 version_conflict` → `409 cohort_name_taken`. A
no-op answers `200` with no version change and no audit. The template, the
subject and the snapshot never change. M4-21 adds `422
max_students_below_enrolled`.

**Lock order (G1):** students → zone → template `FOR SHARE` (only in the
cohort create) → group **or cohort** `FOR UPDATE` → series `FOR UPDATE` →
sessions by id → offer. The nightly extension of a cohort's series and a
series offer on it take the cohort row (`courses.service.lock_cohort`)
where a group series takes the group row; M4-21 enrollment takes it after
the students and the zone. The `courses` module never takes a scheduling
lock.

**Price (E6).** A cohort has no price yet: M6-04 / M6-07 add the
`course_cohorts` price link (a nullable foreign key to the price catalog)
when the catalog exists.

### Session requests

Implemented by M4-14 (#59; spec §1 "request, not booking", §3, §15
W2.1-W2.2; ADR-0005 D1, D2, D9; ADR-0007 D17; ADR-0008 amendment) in
`apps/api/app/modules/scheduling/` (`requests.py`: the reads and writes;
`requests_policy.py`: the family record policy; `family_slots.py`: the
pure slot rules; `request_approval.py`: the inbox type;
`requests_router.py`: the routes). The web pages are `/requests` and
`/requests/new`. M4-15 (#60) adds the staff review
(`scheduling/request_review.py`, the workflow
`app/workflows/confirm_session_request.py` and its routes in
`app/workflows/session_requests_router.py`; web page
`/schedule/requests/:id`).

A guardian (for a linked child) or a student (for themselves) sees the free
times for an **individual** session of a subject and length, and requests
one. A request is `pending` until staff decide it; it creates **no session
and holds no time**: other families, the slots and staff scheduling are
unaffected.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `GET /api/v1/available-slots` | `authenticated()` + record policy; `rate_limit("available_slots.user")` | `?student_id&subject_id&duration_minutes&from&to`; `200` with `AvailableSlots`. Read-only, at most 14 statements. |
| `POST /api/v1/session-requests` | `authenticated()` + record policy; `rate_limit("session_request.user")` | `{student_id, subject_id, starts_at, duration_minutes, note?}`; `201` with `SessionRequestDetail`. |
| `GET /api/v1/session-requests` | `authenticated()` + record policy | `?student_id&status=pending\|confirmed\|rejected\|withdrawn\|all` (default `all`), the #21 offset page `{items, total, limit, offset}` (`limit` 1-100, default 25; `offset` from 0); newest first; at most 5 statements. |
| `GET /api/v1/session-requests/{request_id}` | `authenticated()` + record policy | `SessionRequestDetail`. |
| `POST /api/v1/session-requests/{request_id}/withdraw` | `authenticated()` + record policy + the requester | `{version}`; `200` with `SessionRequestDetail`. |
| `GET /api/v1/session-requests/{request_id}/review` | `require("schedule.manage")` | Staff view; `200` with `SessionRequestReview`; at most 6 statements (M4-15). |
| `POST /api/v1/session-requests/{request_id}/confirm` | `require("schedule.manage")`; an `override` also needs `schedule.override` | `{version, tutor_user_id, classroom_id?, respond_by?, override?}`; `200` with `{request: SessionRequestReview, class_session: ClassSessionCreated}` (M4-15). |
| `POST /api/v1/session-requests/{request_id}/reject` | `require("schedule.manage")` | `{version, reason}`; `200` with `SessionRequestReview` (M4-15). |

The family routes need no permission (ADR-0005 D9: families reach their
schedules through record policies); the three staff review routes need
`schedule.manage` (Owner, Administrator; ADR-0005 D9 "review session …
requests"), so callers without it get `403 permission_denied`.

**Record policy.** A caller may act for a student when they hold
`guardian` and the student is linked to their **active** guardian profile
(for an **adult** student, only with that student's
`guardian_academic_access` consent naming their profile: otherwise `403
student_consent_required`), or they hold `student` and it is their own
profile. Anyone else (staff, tutors, an unlinked or deactivated guardian)
gets the same `404 not_found` as an unknown id. Lists filter (an empty page
for a `student_id` the caller can't act for); details hide (`404`).

**Available slots.** `from`/`to` are local dates in the center zone: `to >=
from` (`range_invalid` on `to`), at most 14 dates (`range_too_long` on
`to`), `from >=` today (`range_in_past` on `from`), `to <=` today + 60 days
(`range_too_far` on `to`); `duration_minutes` is 30, 45, 60, 90 or 120.
Order: `401` → `429` → `422` (schema, range) → `404` / `403` → `422`
references (`student_inactive`, `subject_unknown`, `subject_inactive`) →
`200`. Candidates start on a **30-minute local grid** (`:00` / `:30`) from
the weekday's opening time (rounded up) to the last start ending by the
closing time; closed weekdays and closure dates have none; a start in a
spring-forward gap is skipped (`fold=0`). A candidate is shown when the
booking notice is met (#61), it is within the operating hours on an open
date, the student has no overlapping session in a student-occupying state,
and **at least one** tutor is eligible (approved qualification, active
`tutor` account, weekly windows covering it with no time off, no
overlapping session as tutor). **Slots are tutor-agnostic:** no tutor id,
name or count is returned. Rooms, workload warnings and pending requests
are not considered. Response: `{time_zone, student_id, subject_id,
duration_minutes, from, to, days: [{date, slots: [{starts_at, ends_at,
already_requested}]}]}`, every date of the range in order;
`already_requested` marks a slot overlapping one of this student's pending
requests (a hint, not occupancy).

**Create order:** `401` → `429` → `422` schema (unknown fields such as
`tutor_user_id`, `classroom_id` or `ends_at` included) → `404` / `403` → the
per-student lock → the zone `FOR SHARE` → `422` (`naive_datetime`,
`not_whole_minutes`, `crosses_local_date`, `not_on_grid` on `starts_at`;
`student_inactive`; `subject_unknown` / `subject_inactive`) → `409
session_request_limit_reached` (5 pending per student) → `409
session_request_overlap` → the slot check for exactly that time (`409
slot_unavailable`, opaque) → the request and its inbox item → `201`. The
`note` is trimmed (at most 500 characters; empty becomes `null`). No
notification is sent.

**Withdraw order:** `422` → `404` → `403 permission_denied` (not the
requester; a co-guardian sees the request but can't withdraw it) → the row
`FOR UPDATE` → `409 version_conflict` → `409 session_request_not_pending` →
`withdrawn` with `resolved_at`, and its inbox item `cancelled`. A withdrawn
request frees the student's cap and time.

**Detail** (`SessionRequestDetail`, also the list items): `id`, `student
{id, display_name}`, `subject {id, name, education_level_name}`,
`starts_at`, `ends_at`, `duration_minutes`, `status`, `note`,
`requested_by_me`, `can_withdraw` (the requester, while `pending`),
`created_at`, `resolved_at`, `version`, `rejection_reason` (only when
`rejected`, M4-15). Family responses are closed allow-lists: never a tutor,
room, session, another student or the requester's name, and the `409`
bodies carry no `conflicts` or `details`.

**Staff review (M4-15, #60; spec W2.3-W2.4).** `SessionRequestReview`:
`id`, `status`, `version`, `created_at`, `resolved_at`, `student {id,
display_name, is_active}`, `subject {id, name, education_level_name,
is_active}`, `starts_at`, `ends_at`, `duration_minutes`, `note`,
`requested_by {id, display_name}`, `approval_request_id`,
`class_session_id` (when `confirmed`), `rejection_reason` (when
`rejected`), `can_decide` (`pending` and not started). Review order: `401`
/ `403` → `404` → `200`.

**Confirm order** (one Unit of Work): `401` / `403` (`schedule.manage`) →
`422` schema (`tutor_user_id` is required: a request is never confirmed
unassigned) → `403 permission_denied` for an `override` without
`schedule.override` → `404` → the student lock → the zone `FOR SHARE` → the
request `FOR UPDATE` → `409 version_conflict` → `409
session_request_not_pending` (`details.status`) → `409
session_request_started` → the **Class sessions create path** for the
request's student, subject and time with the chosen tutor and room (its
`422` references, every conflict, the override and the `EXCLUDE` race; the
`409` / `422` bodies pass through unchanged and the request stays
`pending`) → the request `confirmed` with `class_session_id` and
`resolved_at` → its inbox item `approved` (through the #40 core) → the
requester's notification → `200`. **The booking notice is measured from
the request's `created_at`**, with the scheduling policy in force then (the
family met it when asking); every other check runs at confirmation time.
**The session is `offered`** to the chosen tutor (one `pending` offer,
`offered_by` the confirming user, default `respond_by` as for Class
sessions, the tutor's `scheduling.offer_received`); a later decline or
expiry follows Assignment offers (`unassigned` plus a reassignment item)
and the request **stays `confirmed`**. Lock order: students → zone →
request → inbox item. M6-06 adds the session's charge in this Unit of
Work.

**Reject order:** `401` / `403` → `422` (`reason` trimmed, 1-1000:
`missing` / `string_too_long`) → the request `FOR UPDATE` → `404` → `409
version_conflict` → `409 session_request_not_pending` → `rejected` with
`rejection_reason` and `resolved_at` → its inbox item `rejected` with the
same reason → the requester's notification → `200`. Lock order: request →
inbox item (as the withdraw). Rejecting after the requested time has
started is allowed; it frees the student's pending cap. Pending requests
are never expired automatically.

**Notifications:** `scheduling.session_request_confirmed` and
`scheduling.session_request_rejected`, params `{session_request_id,
student_name, subject_name, starts_at}` (never a tutor, room, session id or
the reason), to the **requester only**, and only while their account is
active and they may still act for the student by the record policy above;
otherwise none is sent and the decision still succeeds. Other guardians and
the student see the status in their requests.

**Inbox type** `scheduling.session_request`: reviewed by `schedule.manage`
holders (Owner, Administrator), no request-changes, a rejection needs a
reason; the summary is `{student_name, subject_name,
education_level_name, starts_at, ends_at, duration_minutes, requested_by,
note, status, class_session_id, session_request_id}`. The generic approve and reject can't
decide it (they carry no tutor): they succeed only for a request the review
routes already confirmed or rejected, otherwise `409
session_request_review_route_required` with `details.review_path` and the
item stays pending. Its items list no `allowed_actions` (the inbox
shows a "Review" link instead): a handler may set `inbox_decisions = False`
for a type decided on its own page.

**Limits and data.** `available_slots.user` 60 per 15 min and
`session_request.user` 10 per hour, per signed-in user (see Rate limits).
`session_requests` rows are audited (`create`/`update`); pending ones block
a change of the center's time zone (`scheduling.session_requests`).

### Change requests

Implemented by M4-17 (#62; spec §2, §3 "cancellations and rescheduling follow
configurable notice/no-show/makeup policies, with user requests and admin
approvals, exception handling, and audit trails"; ADR-0005 D2, D6, D9;
ADR-0008 amendment) in `apps/api/app/modules/scheduling/`
(`change_requests.py`: create, reads, withdraw; `change_requests_policy.py`:
the record policy; `change_request_outcome.py`: the outcome rules;
`change_request_review.py`: the staff review and decisions;
`change_request_approval.py`: the inbox type; `change_requests_router.py`:
the family and tutor routes), the workflow
`app/workflows/change_request_decision.py` and its routes in
`app/workflows/change_requests_router.py`. The staff page is
`/schedule/change-requests/:id`; the family and tutor pages are #62b
(M4-17b).

A guardian, a student or the tutor asks to **cancel** or **reschedule** one
upcoming **confirmed individual** session. The scheduling policy's outcome
is recorded when the request is made; staff approve it (the change goes
through the existing services), approve it **as an exception**, or reject
it.

| Route | Access | Notes |
| ----- | ------ | ----- |
| `POST /api/v1/change-requests` | `authenticated()` + record policy; `rate_limit("change_request.user")` | `{class_session_id, kind: cancellation\|reschedule, proposed_starts_at?, note?}`; `201` with `ChangeRequestDetail`. |
| `GET /api/v1/change-requests` | `authenticated()` + record policy | `?student_id&class_session_id&status=pending\|approved\|rejected\|withdrawn\|all` (default `all`); the #21 offset page (`limit` 1-100, default 25); newest first; at most 5 statements. |
| `GET /api/v1/change-requests/{change_request_id}` | `authenticated()` + record policy | `ChangeRequestDetail`. |
| `POST /api/v1/change-requests/{change_request_id}/withdraw` | `authenticated()` + record policy + the requester | `{version}`; `200` with `ChangeRequestDetail`. |
| `GET /api/v1/change-requests/{change_request_id}/review` | `require("schedule.manage")` | `200` with `ChangeRequestReview`; at most 6 statements. |
| `POST /api/v1/change-requests/{change_request_id}/approve` | `require("schedule.manage")`; an `override` also needs `schedule.override` | `{version, exception?, reason?, classroom_id?, override?}`; `200` with `{change_request: ChangeRequestReview, class_session: ClassSessionDetail, warnings}`. |
| `POST /api/v1/change-requests/{change_request_id}/reject` | `require("schedule.manage")` | `{version, reason}`; `200` with `ChangeRequestReview`. |

No permission is added: families and tutors act through the record policy
(ADR-0005 D9), staff through `schedule.manage` (Owner, Administrator; a
Coordinator or Finance user gets `403`). Staff can't create requests: they
edit the schedule directly (Class sessions).

**Record policy (S1/S2).** A caller may request a change of session X when
they hold `tutor` and teach X (`initiated_by = "tutor"`; a user who is both
X's tutor and a family member acts as the tutor), or X is family-visible
(`confirmed` or `completed`) and the caller may act for one of its
participants by the Session requests record policy (`initiated_by =
"family"`, `student_id` = that participant; a linked guardian of an adult
without the consent gets `403 student_consent_required`). Anyone else
(another family, another tutor, staff, a session families can't see yet)
gets the same `404 not_found` as an unknown id. **Reads:** a family caller
sees the `family` requests of the students they may act for (so the child's
other guardians see them too); a tutor sees only the requests they made.
**Tutor requests are never visible to families.** Lists filter (unknown or
foreign filters give an empty page); details hide (`404`). Only the
requester may withdraw (anyone else gets `404`).

**Create order** (one Unit of Work): `401` → `429` → `422` schema
(`proposal_required` / `proposal_not_allowed` on `proposed_starts_at`; a
`note` over 500 characters) → the session and its participants, unlocked,
and the record policy (`404` / `403`) → for a reschedule the zone `FOR
SHARE` → the session `FOR UPDATE` → `409 change_request_kind_unsupported`
(`details.kind`: group and course sessions are refused, cancelling for one
student would cancel everyone's class) → `409 class_session_not_changeable`
(`details.state`) unless `confirmed` → `409 change_request_session_started`
→ `409 change_request_pending` (`details.change_request_id` only when the
caller may read it) → for a reschedule `422` on `proposed_starts_at`
(`naive_datetime`, `not_whole_minutes`, `crosses_local_date`, ...;
`proposal_in_past`; `proposal_unchanged`) and, **for a family**, the
Session requests slot rule at the proposed time (tutor-agnostic: the
student's sessions, the center's hours and closures, the booking notice
from now, at least one eligible tutor) with the session itself left out →
`409 slot_unavailable` (opaque) → the classification → the request and its
inbox item → `201`. A **tutor's** proposal is checked for the bounds only.
The proposed end is the proposed start plus the session's length (the
length never changes). No staff notification: the inbox item is the signal.

**Classification (request time, never recomputed).** The scheduling policy
version in force when the request is made classifies it
(`administration.service.classify_change`, see Scheduling policies) for the
session's start at that time: a family request is `on_time` (exactly the
`change_notice_hours` before the start included), `late`, or `no_show`
(never, since a started session is refused); a tutor request is always
`excused`. A cancellation allows a makeup per the policy's flags (until the
start + `makeup_window_days`); a reschedule never does. The request stores
`policy_id`, `classification`, `makeup_allowed`, `makeup_until` and the
session's times; later policy versions or edits never change them.

**Detail** (`ChangeRequestDetail`, also the list items; a closed allow-list):
`id`, `kind`, `status`, `class_session {id, starts_at, ends_at, subject {id,
name, education_level_name}}` (live: the new times once a reschedule is
approved), `student {id, display_name}`, `proposed_starts_at`,
`proposed_ends_at`, `note`, `outcome {classification, makeup_allowed,
makeup_until}` (the effective outcome once `approved`, otherwise the
request-time one, "as requested"), `decision {exception, reason,
decided_at} | null` (once approved or rejected; the requester sees the
reason), `requested_by_me`, `can_withdraw`, `created_at`, `resolved_at`,
`version`. Never a tutor, a classroom, another participant, the requester's
name, a `policy_id` or conflict details; the `409` bodies above carry only
the `details` named.

**Withdraw order:** `422` → `404` (not visible, or not the requester) → the
request `FOR UPDATE` → `409 version_conflict` → `409
change_request_not_pending` → `withdrawn` with `resolved_at`, and its inbox
item `cancelled`. Allowed after the session has started.

**Staff review** (`ChangeRequestReview`): the detail's facts plus
`initiated_by`, `requested_by {id, display_name}`, `session {id, state,
kind, starts_at, ends_at, series_id, tutor, classroom, version}` (live),
`session_starts_at` / `session_ends_at` (when asked), `policy {id,
effective_from}`, `request_outcome`, `final_outcome` (once approved),
`outcome`, `decided_by`, `approval_request_id` and `can_decide` (`pending`,
the session still `confirmed` and not started).

**Approve order** (one Unit of Work): `401` / `403` → `422` schema (`reason`
trimmed, at most 1000; `reason_required` with `exception`) → `403
permission_denied` for an `override` without `schedule.override` → `404` →
`422 classroom_not_applicable` / `override_not_applicable` on a
cancellation → the locks: a **cancellation** takes the series `FOR UPDATE`
(when there is one) → the session `FOR UPDATE`; a **reschedule** the
students' lock → the zone `FOR SHARE` → the series `FOR SHARE` → the
session `FOR UPDATE`; then the request `FOR UPDATE` → `409
version_conflict` → `409 change_request_not_pending` (`details.status`) →
`409 change_request_session_started` → `409 change_request_session_changed`
(`details.state`) → `422 exception_not_applicable` → the change:

- **cancellation:** "Cancelling a session" with `initiated_by` = the
  request's, the reason `family_request` / `tutor_request`, the
  `change_request_id` and the requester left out of
  `scheduling.session_cancelled`;
- **reschedule:** "Moving a session" to the proposed times (room kept, or
  `classroom_id`), every check live with the session's own tutor (overlaps
  without the session itself, availability, qualification, closures,
  hours, room) **except the booking notice, measured from the request's
  `created_at`** with the policy in force then; `override` as for `PATCH`.
  A series occurrence becomes an exception. The state and the tutor are
  kept. The requester is left out of `scheduling.session_rescheduled`. A
  `409 scheduling_conflict` / `422` passes through unchanged and the request
  stays `pending`;

→ the effective outcome → `approved` (`exception`, `decision_reason`,
`decided_by_user_id`, `resolved_at`) → the named audit event
`scheduling.change_request_decided` `{change_request_id, class_session_id,
kind, initiated_by, decision, exception, classification,
final_classification, final_makeup_until, policy_id, reason}` → the seam of
a reschedule → the inbox item `approved` → the requester's notification →
`200`. Approving after the session has started is refused; there is no
automatic close of pending requests (staff reject them, or the requester
withdraws).

**Exception.** `exception: true` (with a reason) approves the request as
`excused`: the effective outcome is the stored policy version's `excused`
outcome for the same start and request time, so a cancellation gets a
makeup until the start + `makeup_window_days` and a reschedule none. The
request-time outcome is kept beside it. An exception on a request that is
already `excused` (every tutor request) is `422 exception_not_applicable`.
Staff who would rather find a substitute for a tutor's request use
Substitutions and reject the request with a reason.

**Reject order:** `401` / `403` → `422` (`reason` trimmed, 1-1000:
`missing` / `string_too_long`) → the request `FOR UPDATE` → `404` → `409
version_conflict` → `409 change_request_not_pending` (`details.status`) →
`rejected` with the reason → the audit event (`decision: "rejected"`) → the
inbox item `rejected` with the same reason → the requester's notification →
`200`. The session is untouched; allowed after the start. Lock order:
request → inbox item (as the withdraw).

**Other writers** (a staff cancel or move, series edits, closures,
substitutions) leave pending requests alone; the approval re-checks the
session and `can_decide` shows staff that it is stale.

**Notifications.** The requester only, while their account is active and,
for a family requester, they may still act for the student:
`scheduling.change_request_approved` `{change_request_id, kind, starts_at,
new_starts_at, makeup_until, exception}` and
`scheduling.change_request_rejected` `{change_request_id, kind, starts_at}`
(`starts_at` is the session's start when asked; never the reason, a tutor,
a room or the classification). Everyone else affected gets the services'
`scheduling.session_cancelled` / `scheduling.session_rescheduled`, without
the requester; the deciding staff member is never notified. A tutor's
approved cancellation reaches the family as `session_cancelled`, without
saying who asked.

**Inbox type** `scheduling.change_request`: reviewed by `schedule.manage`
holders, no request-changes, a rejection needs a reason, `high` priority
when the session starts within 24 hours of the request. Its items list no
`allowed_actions` (the inbox shows a "Review" link); the generic approve
and reject answer `409 change_request_review_route_required` with
`details.review_path` unless the review routes already decided it. The
summary (three statements) is `{change_request_id, kind, initiated_by,
student_name, subject_name, education_level_name, starts_at,
proposed_starts_at, classification, status, class_session_id}`.

**Hooks for billing and pay.** `app/workflows/session_cancellation.py`
`on_session_cancelled` receives the `change_request_id` (else `None`), and
`app/workflows/session_reschedule.py` `on_session_rescheduled(session, *,
class_session_id, previous_starts_at, previous_ends_at, initiated_by,
change_request_id, by_user_id)` runs after an approved reschedule; both are
no-ops today. M6-14 (billing) and M7-04 (tutor pay) add their steps there
and read the effective outcome with
`scheduling.service.get_change_request_outcome(session, change_request_id)`.
A rejected or withdrawn request has no money effect.

**Limits and data.** `change_request.user` 10 per hour per signed-in user,
every request counted (see Rate limits). `session_change_requests` rows are
audited; one `pending` request per session (a partial unique index); pending
**reschedule** requests block a change of the center's time zone
(`scheduling.change_requests`).

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
