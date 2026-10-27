# ADR-0007: Authentication, sessions, recovery and delegated permissions

## Status

Accepted

## Date

2026-10-18

## Sign-off

Owner: JaimeSolisS. Decision items D1 and D4–D14, D17 and D18 approved as recommended (option (a) for each) on 2026-10-18 (GitHub issue #19, M2-01, owner decision comment). No amendments. The approval includes the scope effects of D14 (self-service password change added to #30) and D17 (birthdate required in #33).

Amended 2026-10-18: D9 token derivation instead of a stored hash (issue #24, owner decision E1).

## Amendments

- 2026-10-18, GitHub issue #29 (M2-11), owner decision: invitation and reset tokens are shown as a link only. No QR code is generated, so no QR library is added.
- 2026-10-18, GitHub issue #31 (M2-13), owner decision E2 (a): managing an account needs authority to assign every role it holds, and nobody suspends or archives themselves (D4).
- 2026-10-19, GitHub issue #195, owner request: the local demo seed has one demo account per role with documented development passwords (APP_ENV=local only), plus demo families.
- 2026-10-24, GitHub issue #303 (owner-delegated decision E1 (a)): the local demo seed also has a second tutor account, `demo.tutor2`, with a documented development password (APP_ENV=local only), for the course and substitution demo data.

## Context

Resolves architecture §10 **ISSUE-G2** (§10.1, "Session" names two entities) and **ISSUE-S1, S2, S4, S5, S6 and S7** (§10.7, identity, security and privacy) in [`../architecture.md`](../architecture.md). ISSUE-S3 (rate limiting) is resolved separately in [ADR-0008](0008-rate-limiting-without-redis.md). Records decision items **D1, D4–D14, D17 and D18** of issue #19.

- Spec [`../spec.md`](../spec.md) §2 defines the predefined roles, record-level ownership checks, password reauthentication for sensitive actions and the user states `pending`, `active`, `suspended` and `archived`. It says some actions belong to the "Owner or authorized Administrator" (§7), which implies per-user grants beyond the fixed roles, and §12 lists `UserRole`, which implies several roles per user.
- Spec §10 (Quality and security) requires secure server sessions in HTTP-only cookies, CSRF protection, session expiration and revocation, and password reauthentication. Sessions live in PostgreSQL; Redis is excluded (§10, §14).
- Spec §1 and §14 rule out product email, yet account activation and password recovery need a delivery mechanism. Spec §18 item 2 leaves that mechanism open and asks for an explicit implementation decision. This ADR is that decision. It does not change spec §1 or §14, so the spec is not changed.
- Spec §9 asks for self-registration requests with administrator approval, guardian consent records, privacy-notice acceptance and controlled access to minors' data. Spec §18 item 9 leaves the legal consent wording open.
- Spec §12 uses `Session` both for a login session (Identity) and for a class occurrence (Scheduling).
- The spec does not choose a password hashing algorithm, a password policy, a login identifier, session timeouts, the CSRF token delivery or the reauthentication list.

## Decision

### Naming (ISSUE-G2, D1 (a))

- A login session is an `AuthSession`, table `auth_sessions`.
- A class occurrence is a `ClassSession`, table `class_sessions`, API path `/api/v1/class-sessions`.

### Roles and delegated permissions (ISSUE-S2, D4 (a))

- Each role's permission set is fixed in code and seeded.
- A user may hold several roles. Effective permissions are the union of the user's roles plus their delegations. Record-level policies still apply (for example, a tutor who is also a parent sees their own children as a guardian and their students as a tutor).
- **Delegable permissions**, granted only by an Owner to individual Administrators: `payroll.finalize`, `schedule.override`, `finance.refund.approve`, `user.admin_role.assign`. (`user.admin_role.assign` replaces the proposed `user.role.assign`.)
- **Role assignment:**
  - Administrators assign and remove Academic Coordinator, Finance Staff, Tutor, Student and Parent/Guardian.
  - The Administrator role is assigned or removed only by an Owner or by a holder of `user.admin_role.assign`.
  - The Owner role is assigned or removed only by an Owner and cannot be delegated.
  - An invitation that carries a role counts as assigning that role.
  - The last active Owner cannot be removed or suspended.
- **Managing an account** — profile, suspend, archive, reactivate — needs authority to assign every role it holds; nobody suspends or archives themselves. (amended by #31)

### Password hashing (D5 (a))

- Argon2id through `argon2-cffi`. The dependency is approved here and added in #20 (M2-02).
- Parameters: memory 19 MiB (19456 KiB), 2 iterations, parallelism 1, 16-byte salt, 32-byte hash.
- Hashes are stored in the standard PHC string format.
- On a successful login the stored hash is upgraded when `check_needs_rehash` reports changed parameters.
- Plaintext passwords are never logged, audited or returned.

### Password policy (D6 (a))

- 12 to 128 characters after Unicode NFKC normalization. Any characters are allowed.
- No composition rules and no periodic expiry.
- A password equal to the username is rejected.
- No lookup against breach APIs (there is no outbound traffic).

### Login identifier (D7 (a))

- Users log in with a unique, case-insensitive **username** of 3 to 64 characters from `a–z 0–9 . _ -`, chosen when the account is created through an invitation or a registration request.
- Email and phone are optional contact fields and are never used to log in.
- Login errors do not reveal whether an account exists.

### Sessions (ISSUE-S4, D8 (a))

- **Cookie:** `__Host-classora_session` with `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/` and no `Domain`.
- **Token:** 32 random bytes. The server stores only its SHA-256 hash.
- **Timeouts:**
  - Staff (Owner, Administrator, Academic Coordinator, Finance Staff): 30 minutes idle, 12 hours absolute.
  - Tutors, students and guardians: 7 days idle, 30 days absolute.
  - A user with several roles gets the strictest class.
- **Rotation and revocation:**
  - A new session ID is issued on every login.
  - All of a user's sessions are revoked on suspend, archive, role change or password reset.
  - A self-service password change revokes every other session and rotates the current one.
- **Writes:** `last_seen_at` is written at most once per minute.
- **Errors:** an expired session returns `401` with code `session_expired` (as #25 requires).
- **Local development:** a setting for plain-HTTP local runs switches to the cookie name `classora_session` without `Secure`. Settings validation refuses that setting outside the local environment.

### CSRF (ISSUE-S4, D9 (a))

- **Token:** a synchronizer token derived from the session token (base64url of HMAC-SHA256 keyed with the session token), so nothing extra is stored; it is returned in the JSON of `POST /api/v1/auth/login` and `GET /api/v1/auth/me`. The SPA keeps it in memory and sends it as `X-CSRF-Token` on POST, PUT, PATCH and DELETE. It is never in a cookie that JavaScript can read; after a reload the SPA calls `/auth/me` again.
- **Origin:** `Origin` must equal the configured app origin. A missing or foreign `Origin` returns `403`.
- **Unauthenticated endpoints:** login, token redemption and registration rely on the `Origin` check plus `SameSite`.

### Reauthentication (ISSUE-S5, D10 (a))

- **Actions that require password reauthentication:**
  - role, permission and delegation changes, including creating an invitation for a staff role (Owner, Administrator, Academic Coordinator, Finance Staff);
  - suspending or archiving a user;
  - issuing password-reset tokens;
  - payroll finalization;
  - refunds and reversals;
  - financial corrections;
  - retention and permanent-deletion actions;
  - business-settings changes to payment methods or time zone;
  - bulk exports of personal data.
- **Window:** 5 minutes from the reauthentication, not extended by activity. It belongs to the session.
- **Self-service password change:** asks for the current password instead.
- **Registration in code:** #26 (M2-08) registers the full list in code, including the actions of later milestones. Those endpoints must use it, and the permission-matrix test checks them as they appear.

### Invitations and password resets without email (ISSUE-S1, D11 (a))

- An authorized staff member generates a **single-use** token in the app: 32 random bytes, stored only as a SHA-256 hash.
- The app shows it once, as a link (no QR code). The staff member hands it over **out of band** (in person, or through the center's existing phone contact).
- Invitation and reset tokens both expire after **24 hours**.
- Issuing a new token invalidates any unredeemed token of the same kind for that user.
- A used or expired token returns `410`.
- No email is sent. This resolves spec §18 item 2 without changing spec §1 or §14.

### Privileged recovery and Owner bootstrap (D12 (a))

- **Who may reset whom:** issuing a reset needs the `user.password_reset.issue` permission (Owner and Administrator) plus reauthentication. Only an Owner may issue resets for Owner or Administrator accounts. Nobody issues a reset for their own account; they use the self-service change.
- **Bootstrap:** `python -m app.cli bootstrap-owner --username …`, wrapped as a `make` target, creates the first Owner. It works only when no Owner exists, reads the password from an interactive prompt or stdin, and hashes it as above.
- **Owner reset:** `reset-owner-password`, run only from the host shell, issues a reset token for an existing Owner and prints it. In AWS the host shell is reached through SSM, so access to it already proves operator control.
- **Audit:** both commands write audit events.
- **Demo seed:** the local demo seed has a demo account per role with a documented development password, and the seed refuses to run outside the local environment.

### Self-registration (D13 (a))

- Only **Parent/Guardian** or **Student** can be requested. Staff and tutors join only by invitation.
- The requester chooses a username and password when submitting the request. The account stays `pending`, unable to log in, until an Administrator approves it in the approval list, confirming the person's identity out of band.
- Approval makes the account `active` with the requested role. Rejection archives the request.
- Registration never links students and never enrolls anyone.

### Self-service password change (D14 (a))

- `POST /api/v1/auth/password` takes the current and the new password, applies the password policy and the session rotation and revocation rules above, and writes an audit event.
- It is built in #30 (M2-12, renamed "Password reset and change").

### Adult students (ISSUE-S6, D17 (a))

- For minors, linked guardians have academic access by default.
- On the student's 18th birthday, computed from the birthdate against today's date in the center's time zone, guardians lose **academic** visibility automatically unless a consent from the student is recorded. Staff can record that consent for the student, noting the method (on paper or in person), or a student with a login can give it in the app.
- **Billing** visibility follows the family billing account, whatever the student's age.
- The birthdate is **required** on the student profile.
- Legal wording stays open for M10-07 (spec §18 item 9).

### Logins for minor students (ISSUE-S7, D18 (a))

- A login for a student under 18 needs a **consent record** from a linked guardian: who, when, the privacy-notice version, the scope and the method (`in_app`, `paper` or `in_person`).
- A guardian with a login records it in the app, or staff record it for the guardian with method `paper` or `in_person`.
- Revoking the consent suspends the student's login.
- There is no minimum age.

## Consequences

Each M2 issue implements the rules above that it owns:

- **#20 (M2-02):** the user, role, permission and delegation model with seeded role permissions and the delegable list (S2/D4); the module-boundary test ([ADR-0002](0002-module-boundaries-and-reporting.md)); `passwords.py` with Argon2id and the password policy (D5, D6); the username rules (D7); the `bootstrap-owner` command (D12). It adds `argon2-cffi`, the only new runtime dependency from this ADR. It seeds only the permissions M2 uses; later milestones add their own.
- **#23 (M2-05):** login, logout and `AuthSession` with the cookie, token hashing, rotation on login, the local-only cookie setting and rehash on login (G2, D5, D7, D8).
- **#24 (M2-06):** the synchronizer CSRF token and the `Origin` check (D9).
- **#25 (M2-07):** idle and absolute timeouts per role class, `session_expired`, `last_seen_at` throttling and revocation on user-state and role changes (D8).
- **#26 (M2-08):** the reauthentication flow, the 5-minute window and the registered list of actions (D10).
- **#29 (M2-11):** invitation tokens, staff-role invitations behind reauthentication, and self-registration requests (D4, D10, D11, D13).
- **#30 (M2-12, now "Password reset and change"):** reset tokens, who may reset whom, `reset-owner-password`, and the self-service password change (D11, D12, D14).
- **#31 (M2-13):** role assignment rules, delegation grants behind reauthentication and protection of the last active Owner (D4, D10).
- **#33 (M2-15):** the birthdate is required on the student profile (D17).
- **#35 (M2-17):** guardian consent for minor logins, adult-student consent and revocation (D17, D18).

Other effects:

- There is no self-service recovery: a user who forgets their password needs a staff member (or, for an Owner, the operator) to issue a reset token.
- Staff on shared front-desk screens log in again after 30 idle minutes.
- Later milestones' endpoints for the listed sensitive actions must use the reauthentication list from #26.
- Still open: legal wording for consent and privacy notices (M10-07, spec §18 item 9); the general API rate limit ([ADR-0008](0008-rate-limiting-without-redis.md), follow-up #163). Permission assignments for finance and payroll beyond the delegable names are decided in [ADR-0006](0006-financial-ledger-idempotency-and-numbering.md) (ISSUE-F7, #86, M6-01).

## Alternatives considered

- **`LoginSession` and `Lesson`** (D1 (b)). Rejected: "Lesson" doesn't fit group classes or exam-prep courses.
- **Only the Owner, or an Administrator with `user.role.assign`, assigns any role** (D4 (b)). Rejected: it would stop Administrators from doing the user administration spec §2 gives them.
- **No delegation at all** (D4 (c)). Rejected: spec §7 names an "authorized Administrator".
- **Argon2id with RFC 9106's second recommendation** (64 MiB, 3 iterations, 4 lanes; D5 (b)). Rejected: a few concurrent logins could use a noticeable share of the micro instance's 1 GiB of RAM.
- **scrypt from the standard library** (D5 (c)). Rejected: the hash format, constant-time compare and rehash would be our own code.
- **bcrypt** (D5 (d)). Rejected: inputs are limited to 72 bytes, and OWASP lists it only for legacy systems.
- **Minimum 15 characters** (D6 (b)). Rejected: more friction for parents and students.
- **Minimum 8 characters** (D6 (c)). Rejected: too weak for staff accounts with financial access.
- **Email address as the login** (D7 (b)). Rejected: accounts without email need placeholders, and siblings sharing a parent's email collide.
- **Username or email** (D7 (c)). Rejected: two lookup paths and more ways to reveal whether an account exists.
- **A 2-hour staff idle timeout** (D8 (b)). Rejected: front-desk staff screens are shared.
- **One timeout for everyone** (D8 (c)). Rejected: either too lax for staff or too strict for families.
- **A double-submit CSRF cookie** (D9 (b)). Rejected: the token would live in a JavaScript-readable cookie.
- **`Origin` and Fetch-Metadata checks only** (D9 (c)). Rejected: too weak, and #24 requires a token.
- **The proposed reauthentication list only** (D10 (b)). Rejected: #30 and #31 would contradict this ADR.
- **A 15-minute reauthentication window** (D10 (c)). Rejected: longer exposure if a screen is left unlocked.
- **7-day invitations** (D11 (b)). Rejected: the bearer link would live longer in chat apps.
- **Transactional email for authentication messages** (for example Amazon SES; D11 (c)). Considered and rejected: a scope change to spec §1 and §14 that also needs AWS, DNS, SPF and DKIM, which arrive only in Phase 3.
- **One-time recovery codes printed at activation** (D11 (d)). Rejected for now: more UI and more ways to get it wrong; could be a later follow-up.
- **Also showing a QR code** (#29 E1 (a)/(b)). Rejected by the owner: it needs a new dependency, and the link alone is enough for in-person or phone hand-over.
- **Any holder of the reset permission resets any non-Owner account** (D12 (b)). Rejected: one Administrator could take over another.
- **No `reset-owner-password` command** (D12 (c)). Rejected: a sole Owner who forgets their password would have to edit the database by hand.
- **Registration requests without a password, activated by an invitation token** (D13 (b)). Rejected: adds a step for every family.
- **Any role can be requested** (D13 (c)). Rejected: staff roles must never be self-asserted.
- **A separate follow-up issue for the password change** (D14 (b)). Rejected: the change shares `passwords.py` and the reset UI with #30.
- **No self-service password change** (D14 (c)). Rejected: staff would handle every routine password change.
- **Guardians keep access after 18 until the student opts out** (D17 (b)). Rejected: adult students' records would stay visible to guardians without their consent.
- **Guardians lose academic and billing access at 18** (D17 (c)). Rejected: clashes with family billing accounts (ISSUE-F3).
- **Only the guardian records minor-login consent, in the app** (D18 (b)). Rejected: leaves out guardians who have no login.
- **A minimum age for student logins** (D18 (c)). Rejected: not in the spec.

## References

- [`../architecture.md`](../architecture.md) §4.3, §7.1, §10.1 ISSUE-G2, §10.7 ISSUE-S1, S2, S4, S5, S6 and S7, §11, §12
- [`../spec.md`](../spec.md) §1 (no product email), §2 (roles, reauthentication, user states), §7 (authorized Administrator), §9 (registration, privacy, consent), §10 (application stack; quality and security), §12 (domain inventory), §14 (recovery without email), §16 (server-side permissions, privacy), §18 items 2 and 9
- [ADR-0002](0002-module-boundaries-and-reporting.md) (module boundaries), [ADR-0004](0004-money-time-and-date-representation.md) (center time zone, `DATE` birthdates), [ADR-0008](0008-rate-limiting-without-redis.md) (authentication rate limits)
- GitHub issue #19 (this decision gate, items D1, D4–D14, D17, D18); #20–#35 (M2 issues that implement it)
