# Classora

Classora is a management platform for a single tutoring center. It covers class
scheduling and enrollment, attendance, academic records, billing and
payments, tutor payroll and operational reporting for owners, administrators,
coordinators, finance staff, tutors, students and guardians. It is built as a
FastAPI modular monolith with a React single-page client, backed by PostgreSQL.

## Status

**Pre-alpha.** The features below run on a local development stack only;
there is no deployed environment yet. The stack is a FastAPI API with
liveness and readiness health checks, a PostgreSQL database reached through
PgBouncer (schema managed with Alembic migrations), a background worker for
queued and periodic jobs (Procrastinate) and a React + TypeScript + Vite web
app. The web app has a routed shell (layout with navigation, a 404 page and
central error handling), a TanStack Query data layer, a typed API client
generated from the API's OpenAPI document, a design system foundation
(central design tokens, Tailwind CSS, shadcn/ui components catalogued in
Storybook), FullCalendar for the staff calendar and Recharts for progress
charts. Its home page only shows whether the API is live.

No email is sent: invitations and password resets produce a one-time link
that staff hand over in person or by phone. The privacy notice text is a
draft pending legal review.

## Features

Roles: Owner, Administrator, Academic coordinator, Finance staff, Tutor,
Student and Parent/Guardian. "Staff" below means Owners and administrators
unless a bullet says otherwise.

**Accounts and sign-in**

- Everyone signs in with a username and password and signs out from the
  header. Sessions use an HTTP-only cookie and end after a period of
  inactivity or a maximum lifetime.
- Staff invite people from the Invitations page; the invited person opens the
  one-time link, sets their own password and signs in.
- Staff create one-time password reset links from the Password resets page
  (or from a user's page); every signed-in user can change their own password.
- Parents, guardians and students can request an account from the sign-in
  page; it works only after staff confirm their identity and approve it.
- Login, password confirmation, one-time links and account requests are
  rate-limited.
- Every user must accept the versioned privacy notice before using the app,
  and again whenever a new version is published.

**Roles and permissions**

- Each user sees only the pages and navigation their role allows; the API
  checks every request.
- Staff change a person's roles after confirming their password; the person
  is signed out and signs in with the new roles.
- Owners delegate selected permissions to administrators; these take effect
  without signing them out.

**Users**

- On the Users page, staff search and filter every account and edit a
  person's name and optional contact details (never used to sign in).
- Staff suspend, archive or reactivate accounts. Suspending or archiving asks
  for the password again and ends that person's sessions; the last active
  Owner can never be suspended or archived.

**Students, guardians and families**

- On the Students page, staff and academic coordinators keep a profile for
  every student (with or without a login): personal and school details, an
  optional education level and one to five emergency contacts. Profiles are
  deactivated instead of deleted, and the name search tolerates accents and
  typos.
- Finance staff see student profiles without personal or contact details.
  Tutors see only the students of their recent and upcoming sessions.
- On the Guardians page, staff keep a profile for every parent or guardian
  (with or without a login), link it to the guardian's account and link
  guardians to students with a relationship (a child may have several
  guardians, and a guardian several children).
- A signed-in guardian sees only their linked children (minors in full; an
  adult student's personal details only with that student's consent), never
  the child's other guardians; unlinking takes effect on their next request.
- Staff can give a student profile a login. A minor's login works only while
  a linked guardian has consented (in the app, or recorded by staff on paper
  or in person, stamped with the privacy-notice version); withdrawing the
  last consent suspends the login.
- Adult students choose, guardian by guardian, who sees their records. A
  signed-in student sees only their own record.
- On the Families page, staff group students and guardians into families
  (each student and each guardian in at most one family), the grouping that
  family billing will build on. A signed-in guardian sees only their own
  family; being in a family doesn't show a child's records, which still
  follow the guardian's links.

**Catalog and academic calendar**

- On the Catalog page, staff maintain the education levels and the subjects
  taught at each level. Records are deactivated instead of deleted; the
  reference seed creates four example levels (Primaria, Secundaria,
  Preparatoria, Universidad).
- Staff define academic periods (school years, semesters and terms; active
  periods of one type can't overlap) and center closures (holidays, closures
  and vacations), which tell later features whether the center is closed on a
  given day.

**Classrooms**

- In the Catalog, staff keep the classroom list: each room's name, capacity
  and optional weekly availability windows (a room without windows can be
  used at any time).

**Business settings**

- On the Settings page, staff maintain the center name and contact details,
  the weekly operating hours and the center time zone (Mexico City by
  default), which the app uses for every date and time it shows.
- Changing the time zone asks for the password again; it will be refused
  once classes are scheduled.
- On the Scheduling policies tab, staff set the cancellation notice (when a
  cancellation is late and when it is a no-show), when makeups are allowed
  and within how many days, how long tutors have to answer an offer and the
  booking notice. A change takes effect immediately or from a chosen date,
  and earlier versions are kept so past classes keep the rules in force at
  the time. Every signed-in user can read the policy (applying it to
  classes arrives with scheduling).

**Tutors**

- On the Tutors page, staff and academic coordinators see every tutor's
  profile: the account's contact details and professional experience. Staff
  edit it; a signed-in tutor sees and edits only their own contact details
  and experience.
- Qualifications: tutors request to teach a subject (its education level
  follows from it) and can withdraw a pending request; staff approve or
  reject it, with a reason, on the Approvals page, and can later revoke an
  approved qualification with a reason the tutor sees. Only approved
  qualifications count as the tutor's teaching subjects.
- Availability: tutors publish their weekly availability and exceptions (time
  off and extra availability on given dates) in the center's local time;
  staff can edit it too, and academic coordinators view it. The API answers
  whether a tutor is available at a given time, taking center closures into
  account. A change that affects scheduled classes waits for an Owner or
  Administrator to approve it on the Approvals page; approving it flags those
  classes for reassignment.
- Limits and preferences: each tutor's daily and weekly teaching limits, the
  required break between classes (limits will only produce warnings when
  scheduling) and preferred classrooms, edited by the tutor or staff.

**Scheduling**

- Staff (including academic coordinators and finance staff) see every class
  session on the Schedule calendar (FullCalendar), by day, week, month or as
  a list, in the center's time zone, filtered by tutor, student, room and
  class type. Each session shows its state, and is marked when it falls on a
  center closure, its tutor is no longer qualified for the subject, or it was
  booked or moved with an override.
- Tutors, students and guardians see their own schedules under My schedule,
  as a phone-friendly list or a calendar, and guardians choose a child.
  Families see only confirmed and completed classes, and cancelled ones they
  had seen confirmed.
- Owners and Administrators reschedule a session on the calendar by dragging
  it (or resizing its end), or with the keyboard from its "Reschedule"
  dialog; the server checks every move, refused moves snap back, and soft
  conflicts can be overridden with a reason.
- Staff schedule one-off individual sessions from the calendar's "Schedule a
  session" page (one student, a subject, an optional tutor and classroom, a
  date and times in the center's time zone). Before anything is saved the
  server checks student, tutor and classroom overlaps, tutor availability and
  qualification, classroom availability, center closures, operating hours
  and booking notice, and lists every conflict. An Owner, or an
  Administrator with the delegated override permission, can override soft
  conflicts with a reason, which is audited, for one-off sessions and
  recurring series (occurrence by occurrence); overlaps and room capacity
  are never overridable.
- The same page creates weekly recurring series ("Repeats weekly": the days,
  every 1 to 4 weeks, ending never, on a date or after a number of
  sessions). Each series has its own page, where staff move one occurrence,
  change the series from a date onward, or cancel its future occurrences;
  past and completed classes never change.
- When a time conflicts, the page suggests the nearest free times;
  exceeding a tutor's daily or weekly teaching limit, or a too-short break,
  gives a warning but doesn't block.
- The system suggests eligible tutors (qualified, available and free at that
  time) ranked by weekly workload, including replacements for an existing
  session; on the "Schedule a session" page, "Suggest tutors" fills in the
  chosen one.
- A session with a tutor is offered to that tutor; one without a tutor stays
  unassigned. Tutors accept or decline offered sessions from My offers
  (notified in-app), or a whole weekly series in one answer; later sessions
  of an accepted series are confirmed automatically. Declined or unanswered
  offers (48 h by default, set in the scheduling policy) return the session
  for reassignment through the approval inbox, and staff offer it again from
  the session's page (for a weekly series, that session and the following
  ones).
- Staff can hand one upcoming session to a substitute tutor (the substitute
  accepts in My offers; the original tutor and the family are notified) and
  cancel a single session with a reason.
- On the Groups page, staff create ongoing tutoring groups with a weekly
  schedule and a maximum size, and enroll or withdraw students at any time
  (going over the size needs an override). Students see their own groups
  and sessions.
- Guardians and students see available times for individual sessions and
  request one; requests stay pending for staff review and never book a slot
  by themselves. Staff confirm a request by choosing the tutor (the session
  is offered to that tutor) or reject it with a reason, and the requester is
  notified.
- Families and tutors ask to cancel an upcoming confirmed session, or move
  it to another time, from My schedule, and follow their requests under My
  change requests. Staff approve it, approve it as an exception or reject
  it, and the cancellation policy's outcome (on time, late or excused, and
  any makeup allowed) is recorded.
- Once a class starts, its tutor takes attendance from My schedule on a
  phone, marking each student present, late or absent. Staff see it on the
  session page; Owners, Administrators and Academic Coordinators correct a
  mark with a reason, kept in the session's correction history. Students and
  guardians see the student's own attendance on My schedule.
- After the class, the tutor closes it out from My schedule in a guided
  phone flow: attendance, the actual teaching minutes and a short summary
  (topics, performance, homework, next steps). The class becomes completed
  and the closeout goes to the approval inbox for Owners, Administrators and
  Academic Coordinators. From the session page they approve it, send it
  back to the tutor with a reason, or correct the minutes and summary with
  a reason; approvals are kept as an immutable history for payroll, and a
  correction adds a new approval instead of changing the old one. Tutors
  see the reviewer's reason, fix and resubmit a closeout from My schedule,
  and can request a correction after approval, while the approved minutes
  stay in force until it is reviewed. Reviewers also see the classes that
  ended without a closeout and can close one out on the tutor's behalf with
  a reason, or take over a closeout sent back to a tutor who can't act; the
  tutor, the inbox and the session page show that it was submitted on the
  tutor's behalf.
- Tutors keep notes about each student in a class from My schedule. Notes
  are internal and families never see them, until the tutor shares a note:
  then the student and their guardians with access to their academic records
  see it in the class details and are notified. Owners, Administrators and
  Academic Coordinators read every note on the session page and can stop
  sharing one with a reason.
- Owners, Administrators and Academic Coordinators build diagnostic and
  ongoing evaluation templates with up to 20 scored criteria. Tutors record
  evaluations of their students from a phone, with a score and comment per
  criterion and overall feedback, save drafts only they see, and finalize
  them; a correction after that needs an internal reason. Final evaluations
  from every tutor roll up into one academic history per student, grouped by
  school year, semester or term and by subject, or listed by date: staff,
  current tutors, the student and guardians with access to their academic
  records read it, and the student and those guardians are notified of each
  final evaluation. Staff can void an evaluation with a reason.
- Owners, Administrators, Academic Coordinators and a student's current
  tutors build learning plans: objectives with ordered milestones, the
  student's activities and due dates. Tutors work in the subjects they teach
  the student and update milestone progress from a phone. Activating a plan
  shares it with the student and the guardians with access to their academic
  records, who are notified, follow its progress and overdue milestones, and
  are notified again when it is completed.
- Tutors set homework from a phone for one or more of their students or for
  a class, with instructions, a due date (and optional time) and an optional
  maximum score. Students hand in text or a link from their own login and
  can replace it until the deadline; late and missing work is flagged.
  Tutors record work handed in on paper, grade it with a score and feedback
  (a later change needs an internal reason), and scored homework joins the
  academic history. Guardians with access to the student's academic records
  follow each item, the work and the grade; students and guardians are
  notified when homework is set and graded. Owners, Administrators and
  Academic Coordinators read every assignment.
- Tutors write weekly, monthly or on-demand progress reports from a phone,
  next to the attendance, evaluation, homework and learning-plan figures the
  app compiles for the period. A reviewer (Owner, Administrator or Academic
  Coordinator) approves each report, sends it back with a reason or
  retracts it; only approved reports reach the student and the guardians
  with access to their academic records, who are notified, and reasons stay
  internal. Reviewers can also schedule weekly or monthly reports per
  student: after each week or month ends, the tutor gets an empty draft and
  a notification.
- Each student has a progress page for a chosen period (3 to 24 months) and
  subject: attendance, evaluation and homework grade trends by month and
  subject, homework completion, learning-plan progress, and a class log with
  each class's approved summary. Every chart has a text summary and a data
  table. Students and guardians with access to their academic records open
  it from Progress (with the tutor's shared notes per class); Owners,
  Administrators, Academic Coordinators and the student's current tutors open
  it from the student page or My schedule, and a former tutor sees only the
  classes they taught. Tutors and reviewers are told that an approved class
  summary reaches the students and their families.

**Courses**

- On the Courses page, staff and academic coordinators build reusable
  course templates: a name, one or more subjects, the number of sessions and
  their length, ordered syllabus modules (with planned sessions and
  free-text materials references), assessment definitions and completion
  criteria (minimum attendance, required modules and assessments). Templates
  are deactivated instead of deleted; finance staff can read them.
- Staff create a cohort from an active template with its weekly schedule;
  the schedule skips known closures, and the cohort keeps a fixed copy of
  the template's syllabus, so later edits never change it. A cohort is
  offered to a tutor for the whole course, and staff with course access
  browse the cohorts with their dates, status and sessions.
- Staff enroll students in a cohort and withdraw them at any time, also
  after the course has started (a student who joins late gets only the
  sessions that haven't started). Enrollment respects the cohort's maximum
  and the room's capacity; going over the maximum needs an override.
- Tutors, students and guardians open the groups and courses they teach or
  take, read-only (Groups and My courses): tutors see their students' names
  and the sessions they teach, families only their own children's sessions.
  A guardian of an adult student sees them only with that student's consent.
- On a cohort's page, staff and the cohort's tutor see each enrolled
  student's progress against the course's completion criteria (attendance,
  required modules and required assessments) and whether they completed it,
  computed live from the cohort's syllabus; students and guardians see the
  same for their own enrolled students.

**Approvals**

- The Approvals page is the shared list of requests waiting for a decision,
  filtered by status, type and assignee. It holds account requests, tutor
  qualification requests, sessions to reassign after a tutor declined or
  didn't answer an offer, families' session requests (reviewed on their
  own page), families' and tutors' cancellation and reschedule requests
  (also reviewed on their own page), tutor availability changes that
  affect scheduled classes, tutors' session closeouts (reviewed on the
  session page) and progress reports (reviewed on their own page);
  staff approve or reject them (session closeouts and progress reports are
  approved or sent back for changes on their own page, never rejected). Approving an account request
  never enrolls anyone.

**Billing**

- Owners, administrators and finance staff keep a price catalog (Billing →
  Prices) by subject, student level, duration and class type (individual,
  group or course), optionally for one cohort or group. Each price changes
  from a date: a change is scheduled ahead (or marks the service as not
  offered), an upcoming change can be deleted, and existing prices are
  never edited.
- They can turn billing on from a start date, or off again (Billing →
  Settings). While it is on, confirmed and completed individual sessions
  from that date are charged automatically to the student's family, and
  Billing → Unbilled lists the sessions that can't be charged yet (no
  family or no price) with a link to fix the cause.
- They also keep discounts, scholarships, sibling discounts and promotions
  with eligibility rules and validity dates (Billing → Discounts), and
  custom prices per student or family (Billing → Custom prices); posted
  charges keep the price and discounts they were computed with.

**Notifications**

- A bell in the header shows how many notifications are unread and opens a
  panel to read them and mark one or all as read.
- New notifications arrive live (Server-Sent Events); when the live
  connection isn't available, the bell checks every 30 seconds instead.
- Today they tell tutors about qualification decisions and revocations and
  new session offers, tell staff when an offer is declined or expires, and
  tell tutors and families about substitutions and cancelled sessions. In
  local and dev, signed-in users can create a test notification for
  themselves.

**Audit and security**

- Changes to records and security events (sign-ins, passwords,
  invitations) are recorded in an audit log, which staff can read through
  the API (there is no page for it yet).
- Requests that change data are protected by an `Origin` check and a CSRF
  token, and the web app is served with a Content Security Policy and other
  security headers.

**App experience**

- Spanish and English UI text with a language switch, and a light, dark or
  system theme. Signed-in users' language and theme are saved to their
  account and applied when they sign in on any device.
- Pages and components are checked automatically for accessibility (axe) in
  light and dark themes.

**Not built yet:** family balances, payments and the rest of billing,
payroll, reporting and analytics, and deployment. See the
[milestones](https://github.com/JaimeSolisS/classora/milestones) and
[`_docs/tasks.md`](_docs/tasks.md).

**Not in this version:** file uploads and documents (attachments, the shared
academic library, malware scanning, file previews and the recycle bin) are
planned for a later version.

## Prerequisites

- [Docker Engine](https://docs.docker.com/engine/) with Docker Compose v2
- GNU Make
- [uv](https://docs.astral.sh/uv/) (installs the pinned Python version; no other Python install is needed)
- [Node.js](https://nodejs.org/) 24 (see `.nvmrc`; with nvm, run `nvm use`). pnpm
  runs through Corepack, which ships with Node 24, so no global pnpm install is
  needed. Other Node majors are rejected with an engine error.

## Getting started

```bash
cp .env.example .env
make dev
```

`make dev` builds the images and starts PostgreSQL, PgBouncer, the API, the
background worker and the web app in the background, and returns once all of
them are healthy. The API reaches PostgreSQL through PgBouncer (connection
pooling). Then open:

- Web app: <http://127.0.0.1:5173>. It opens the sign-in page; after signing in
  (see [Logging in locally](#logging-in-locally)) the home page shows
  `API: live` when the API is up. Use exactly this address (`127.0.0.1`, not
  `localhost`): it is `APP_ORIGIN`, and the API refuses changes from any other
  origin.
- Liveness: <http://127.0.0.1:8000/health/live>
- Readiness (includes PgBouncer and PostgreSQL): <http://127.0.0.1:8000/health/ready>

The web app calls the API on its own origin: the Vite dev server proxies
`/health` and `/api` to the API. The Compose web service has no hot reload; for
that, run `corepack pnpm --filter @classora/web dev` on the host, which proxies
to `http://127.0.0.1:8000`.

The background worker (`worker`, built from the same image as the API) runs
queued and periodic jobs. It processes jobs only after `make migrate` (below)
has created its tables; until then it waits and logs that it needs
`make migrate`. Follow its logs with `docker compose logs -f worker`. In local
and dev,
`curl -X POST -H 'Origin: http://127.0.0.1:5173' http://127.0.0.1:8000/api/v1/dev/demo-jobs`
queues a demo job to check that the worker runs it.

Stop the stack with `docker compose down` (data is kept in a named volume;
`docker compose down -v` deletes it).

### Compose layout

The services are split into two stack files, the way the servers run them:

- Data stack, `infrastructure/docker/compose.data.yaml`: PostgreSQL (`db`) and
  PgBouncer (`pgbouncer`).
- App stack, `infrastructure/docker/compose.app.yaml`: the API (`api`), the
  background worker (`worker`) and the web app (`web`).

Locally, the root `compose.yaml` combines both into one Compose project, so
plain `docker compose` commands (`ps`, `logs api`, `logs worker`, `exec db psql ...`,
`stop`/`start`, `down`) work from the repository root. The stack files publish
no host ports for PostgreSQL or PgBouncer; only the local developer override,
`compose.override.yaml` (loaded automatically), publishes them on `127.0.0.1`:
PostgreSQL on `POSTGRES_PORT` (default 5432) and PgBouncer on `PGBOUNCER_PORT`
(default 6432). The API and web ports are set in the app stack file, also on
`127.0.0.1`: `API_PORT` (default 8000) and `WEB_PORT` (default 5173).

Live notifications need no extra service: the API listens for them on a
direct PostgreSQL connection (`DATABASE_DIRECT_URL`), and the web dev server
proxies the stream like any other API call.

### Database migrations

```bash
make migrate
```

`make migrate` applies the Alembic migrations to the local Compose database
(it starts the database if it is not running). Migrations connect to PostgreSQL
directly, not through PgBouncer. Neither `make dev` nor the API
runs migrations, so run `make migrate` after `make dev` on a new checkout and
again whenever you pull new migrations. Running it when the database is already
up to date changes nothing.

### Seed data

```bash
make seed       # reference data
make seed-demo  # synthetic demo data (local and dev only)
```

`make seed` applies the reference data every environment needs. Run
`make migrate` first: it refuses to run on a database with pending migrations.
It is safe to repeat; a second run changes nothing.

`make seed-demo` runs on the host (with the API's development dependencies, via
`uv`) and needs `.env`. It applies the reference data and then generates
synthetic Spanish (Mexico) demo data. It never uses real personal data, and it
refuses to run unless `APP_ENV` is `local` or `dev`. Running it again leaves
the demo data as it is; to start over, run
`docker compose down -v`, then `make dev`, `make migrate` and `make seed-demo`.

The reference data currently consists of the user roles and their
permissions. The demo data consists of one demo account per role, plus a
second tutor:

| Username | Password | Role |
|---|---|---|
| `demo.owner` | `classora-demo-owner` | Owner |
| `demo.admin` | `classora-demo-admin` | Administrator |
| `demo.coordinator` | `classora-demo-coordinator` | Academic coordinator |
| `demo.finance` | `classora-demo-finance` | Finance staff |
| `demo.tutor` | `classora-demo-tutor` | Tutor |
| `demo.tutor2` | `classora-demo-tutor2` | Tutor |
| `demo.student` | `classora-demo-student` | Student |
| `demo.guardian` | `classora-demo-guardian` | Parent/Guardian |

These passwords are development placeholders: the accounts, families and the
rest of the demo data exist only when `APP_ENV` is `local` (a `dev` run skips
them). There are also three demo families; `demo.student` is a minor in
`demo.guardian`'s family, with a recorded guardian consent. The rest of the
demo data:

- **Catalog**: example subjects for the four levels, three classrooms, the
  current school year and one holiday in the coming week.
- **`demo.tutor`**: a profile, three approved qualifications and one pending
  request (on Approvals), weekly availability with one day off, teaching
  limits and a preferred classroom.
- **Schedule**, around the day you run it: completed classes in the past week
  and upcoming classes for `demo.student` and a sibling, a weekly series (one
  date skipped for the holiday), a Saturday group, one offer waiting in
  `demo.tutor`'s My offers, one declined offer waiting for reassignment on
  Approvals, and one unassigned session.
- **Courses**: a course template (8 sessions, three modules, two assessments)
  and a cohort taught by `demo.tutor2`, with `demo.student` and another
  student enrolled; it shows in My courses for them, their guardian and the
  tutor.
- **Requests and changes**: `demo.guardian`'s pending session request and
  reschedule request, `demo.tutor`'s pending time-off request (all three on
  Approvals), one class `demo.tutor2` took over as a substitute, and one
  cancelled course class.
- **Classes held and results**: past classes of `demo.tutor` and
  `demo.tutor2` with attendance and approved closeouts; a finished course
  cohort whose completion shows one student completed and two not completed;
  notes shared with `demo.guardian`'s family and one internal note; evaluation
  templates; and evaluations, one still a draft. They show in the session log,
  the academic history, the progress dashboard and the cohort's completion
  (the cohort is under the "ended" or "all" filter of the cohort list).
- **Learning plans, homework and progress reports**: for `demo.student`, an
  active learning plan with one overdue milestone and a completed plan;
  homework that is assigned, handed in and graded; progress reports in draft,
  waiting for review (on Approvals) and approved for the family; and a weekly
  report schedule for `demo.tutor` (the worker adds the next draft after each
  week ends).

The dates stay those of the first run (a second run changes nothing), so reset
with `docker compose down -v`, `make dev`, `make migrate` and `make seed-demo`
to get a fresh schedule. A database seeded before the courses and requests,
the classes held and results, or the learning plans, homework and progress
reports were added keeps its schedule and skips them (`make seed-demo` says
so) until you reset the same way. The pending offer expires after the
scheduling policy's 48 hours and then also waits on Approvals. With classes scheduled, the
center time zone can no longer be changed. `make bootstrap-owner` (below) is
refused once any Owner exists, so locally use either the demo Owner or
`make bootstrap-owner`, not both.

### First Owner account

```bash
make bootstrap-owner USERNAME=<name>
```

`make bootstrap-owner` creates the first Owner account (the center's top-level
administrator). Run `make migrate` and `make seed` first. It prompts twice for
the password, which must have at least 12 characters, and works only while no
Owner account exists.

### Owner password reset

```bash
make reset-owner-password USERNAME=<name>
```

If an Owner is locked out, `make reset-owner-password` prints a one-time link
for that existing, active Owner account. Open it in a browser to set a new
password; it is shown once and expires in 24 hours, and every session of the
account ends when the new password is set.

### Logging in locally

Open the web app at <http://127.0.0.1:5173> and sign in with any demo account
from the table in [Seed data](#seed-data) (for example `demo.owner` /
`classora-demo-owner`, from `make seed-demo`) or with the account from
`make bootstrap-owner`. The first time an account signs in, the
app shows the privacy notice; accept it to continue. To call the API directly,
log in against it and keep the session cookie, for example with the demo
Owner:

```bash
curl -s -c cookies.txt -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:5173' \
  -d '{"username": "demo.owner", "password": "classora-demo-owner"}' \
  http://127.0.0.1:8000/api/v1/auth/login
curl -s -b cookies.txt http://127.0.0.1:8000/api/v1/auth/me
curl -s -b cookies.txt -X POST -H 'Origin: http://127.0.0.1:5173' \
  -H 'X-CSRF-Token: <csrf_token from the login response>' \
  http://127.0.0.1:8000/api/v1/auth/logout
```

Login returns the signed-in user, including a `csrf_token`, and sets the
session cookie; `GET /api/v1/auth/me` returns the same user, roles,
permissions and token, and `POST /api/v1/auth/logout` ends the session. A
failed login answers `401 invalid_credentials`. Until the account has
accepted the current privacy notice (`privacy_notice_required` in the login
response), other API routes answer `403 privacy_notice_required`; accept it
with `POST /api/v1/privacy-notice/acceptance` and `{"version": 1}` (same
headers as logout). After 10 failed logins for one
username, or 50 login attempts from one address, in a 15-minute window, login
answers `429 rate_limited` until the window ends (the `Retry-After` header gives
the seconds to wait), even with the right password. Every `POST`, `PUT`,
`PATCH` or `DELETE` must send `Origin: http://127.0.0.1:5173` (the
`APP_ORIGIN`), and once signed in also the `X-CSRF-Token` header; otherwise
the API answers `403 csrf_failed`. For the same reason the Swagger UI on the
API port cannot send them. Sessions expire after 30 idle minutes (12 hours at
most) for staff accounts such as the demo Owner, and after 7 idle days (30 days
at most) for tutors, students and guardians; `/api/v1/auth/me` then answers
`401 session_expired`, so log in again. Details: [`_docs/api.md`](_docs/api.md) and
[`_docs/security-headers.md`](_docs/security-headers.md).

### Native local stack

For day-to-day work, and for several git worktrees side by side, the API,
worker and web app can run directly on the host against one shared PostgreSQL
container, with no image builds:

```bash
make db-shared                    # start the shared PostgreSQL server (127.0.0.1:55432; idempotent)
make db-create NAME=<name>        # create a database and role <name> ([a-z0-9_]+); prints its URLs
make api-native                   # migrations, then the API with reload on API_PORT
make worker-native                # the background worker
make web-native                   # Vite with hot reload on WEB_PORT
make seed-demo                    # demo data into the same database
make db-drop NAME=<name>          # drop the database and role
make db-shared-down               # stop the server (V=1 also deletes its volume)
```

Run `api-native`, `worker-native` and `web-native` in separate terminals (stop
each with Ctrl-C before `make db-drop`). They read the same keys as Compose
from the checkout's `.env` (`ENV_FILE=<path>` picks another file), and a
variable set in the shell wins over the file. Set `DATABASE_URL` and
`DATABASE_DIRECT_URL` both to the URL `make db-create` prints (there is no
PgBouncer), and `API_PORT`, `WEB_PORT` and `APP_ORIGIN`
(`http://127.0.0.1:<WEB_PORT>`) to ports no other stack uses. The web server
proxies to `API_PROXY_TARGET`, by default `http://127.0.0.1:<API_PORT>`. For
example, two worktrees, each with its own database:

```bash
# worktree A (.env):  API_PORT=8101  WEB_PORT=5274  APP_ORIGIN=http://127.0.0.1:5274
#   DATABASE_URL and DATABASE_DIRECT_URL: postgresql://wt_a:classora_local_only@127.0.0.1:55432/wt_a
# worktree B (.env):  API_PORT=8102  WEB_PORT=5275  APP_ORIGIN=http://127.0.0.1:5275
#   DATABASE_URL and DATABASE_DIRECT_URL: postgresql://wt_b:classora_local_only@127.0.0.1:55432/wt_b
make db-shared && make db-create NAME=wt_a   # in A (B: NAME=wt_b)
make api-native                              # then worker-native, web-native, seed-demo
```

Browsers share cookies between ports of `127.0.0.1`, so sign in to two stacks
from separate browser profiles (or separate `curl` cookie files). The native
stack is not the reference: the Docker stack (`make dev`, `make e2e`) and the
Docker-based CI jobs are, and CI is the parity check on every pull request.

## Configuration

`.env.example` documents every variable with safe local placeholders; copy it
to `.env` as shown above. `.env` is git-ignored and must never be committed.
The API and the worker validate their configuration when they start and refuse
to start on a missing or invalid value; `docker compose logs api` (or
`logs worker`) shows a short `Configuration error` message naming each bad
variable. `WORKER_SHUTDOWN_TIMEOUT_SECONDS` (default 20, 1 to 30) sets how long
a stopping worker lets running jobs finish before aborting them.
`SCHEDULING_HORIZON_WEEKS` (default 12, 4 to 26) sets how far ahead the worker
generates the sessions of recurring series.

`APP_ORIGIN` (required, `http://127.0.0.1:5173` in `.env.example`) is the web
app's origin. Change it together with `WEB_PORT`; `http` is accepted only with
`APP_ENV=local`. An `.env` created before it existed must get the line (or be
refreshed from `.env.example`), or the API refuses to start.

`SESSION_COOKIE_SECURE=false` (the `.env.example` value) is for local plain
HTTP only: it switches the session cookie to `classora_session` without the
`Secure` flag, and the API refuses to start with it unless `APP_ENV=local`.
Every other environment uses the default, `true` (`__Host-classora_session`,
`Secure`). An older `.env` still has `SESSION_COOKIE_SECURE=true`; refresh it
from `.env.example`.

The API uses two database URLs: `DATABASE_URL` is the pooled one (through
PgBouncer) and `DATABASE_DIRECT_URL` the direct one to PostgreSQL (used by
migrations, seeds and the worker). Inside Compose both are built from the `POSTGRES_*`
values; the values in `.env` only matter when you run the API, Alembic or
`make seed-demo` on the host.

If your `.env` was copied before a variable was added, refresh it from
`.env.example` (for example, copy it again).

## Running tests

Docker must be running: API integration tests start a throwaway PostgreSQL
container (and a PgBouncer container built from this repository) with
[Testcontainers](https://testcontainers.com/). The Compose
stack and `.env` are not needed.

```bash
make test              # all tests: API (unit + integration) then web (Vitest)
make test-api          # all API tests with a coverage report
make test-web          # web and API client tests (Vitest, no Docker needed; includes the es/en translation-key check)
make test-unit         # API unit tests only (no Docker needed)
make test-integration  # API integration tests except the migration checks (SHARD=K/N runs one CI shard)
make test-migrations   # API migration checks only (upgrade, round trip, no model drift)
```

Storybook story tests run separately, on the host:

```bash
make test-storybook    # every story in headless Chromium, with axe checks in light and dark
```

The first run downloads the Playwright headless Chromium (about 100 MB); later
runs reuse it.

End-to-end tests (Playwright) run against their own Compose stack:

```bash
make e2e               # smoke, accessibility (axe, light and dark), session-cookie and CSRF, and security-header tests in headless Chromium
```

`make e2e` builds and starts a separate Compose project, `classora-e2e`, on its
own ports (web 15173, API 18000, PostgreSQL 15432, PgBouncer 16432) with an empty
volume, applies the migrations, the reference seeds and the demo data
(`--seed 1`), runs the tests and always removes the stack again (`down -v`).
It needs Docker, uv and Node 24 but no `.env` (it uses `.env.example`), and it
can run while `make dev` is up without touching that stack or its data. A
failed run leaves Playwright traces, screenshots and the Compose logs
(`compose.log`) in `e2e/test-results/` and an HTML report in
`e2e/playwright-report/`. Open a trace from the repository root with
`corepack pnpm --filter @classora/e2e exec playwright show-trace test-results/<test>/trace.zip`
(the command runs in `e2e/`, so the path is relative to `e2e/`).
To keep the stack up for debugging, run `E2E_KEEP=1 make e2e`; the command it
prints removes it afterwards.

Each API run prints a terminal coverage report for the API (`app/`), with
branch coverage and missing lines. `make test` does not start the Compose database,
and it can run while `make dev` is up. `make test` does not run the Storybook or
E2E tests.

Rules for writing tests (layers, fixtures and test data, authorization and
financial invariant tests, E2E and accessibility) are in
[`_docs/testing-guidelines.md`](_docs/testing-guidelines.md).

## Linting and type checks

```bash
make lint      # both apps and the E2E tests
make lint-api  # Ruff (lint and format check) and mypy (strict) on the API
make lint-web  # ESLint, Prettier check and TypeScript (strict) on the web app and the API client
make lint-e2e  # TypeScript (strict) and Prettier check on the E2E tests (e2e/)
```

Lint targets only check and never modify files. The web targets install
dependencies from the committed lockfile first, so no manual `pnpm install` is
needed. `make lint` and `make test` fail if any part fails; use `make -k` to
run every part regardless.

To build the web app for production: `make build-web` (output in
`apps/web/dist/`).

## API client

The web app calls the API through `@classora/api-client`
(`packages/api-client`), a typed client generated from the API's OpenAPI
document. After any API change, regenerate it and commit the result:

```bash
make api-client        # export packages/api-client/openapi.json, then regenerate the client types
make check-api-client  # regenerate and fail if packages/api-client differs from the commit (CI drift check)
```

Both need uv and Node 24, but not Docker, a database or `.env`. API conventions
(paths, Problem Details errors, pagination, versions) are in
[`_docs/api.md`](_docs/api.md).

## Storybook

```bash
make storybook
```

Opens the component catalog at <http://127.0.0.1:6006> (no browser is opened
for you). The toolbar switches stories between light and dark. It fails
instead of using another port when 6006 is taken. To build a static copy:
`corepack pnpm --filter @classora/web build-storybook` (output in
`apps/web/storybook-static/`).

## Continuous integration

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on
every pull request to `develop` or `main` and on pushes to both. Each check is
one `make` target, so a failing check can be reproduced locally with the same
command:

| CI job | Local command |
| --- | --- |
| `api-lint` | `make lint-api` |
| `api-unit` | `make test-unit` |
| `api-migrations` | `make test-migrations` |
| `api-integration (K/4)` | `make test-integration SHARD=K/4` (four parallel shards; the test files are dealt round-robin by path) |
| `web` | `make lint-web`, `make test-web`, `make build-web` |
| `web-storybook` | `make test-storybook` |
| `api-client` | `make check-api-client` |
| `e2e` | `make lint-e2e`, `make e2e` (on failure, uploads traces, the HTML report and the Compose logs as the `e2e-report` artifact) |
| `security-deps` | `make audit-deps` (pip-audit and `pnpm audit` over both lockfiles) |
| `security-secrets` | `make scan-secrets` (gitleaks, full history); on pull requests also `make scan-secrets-diff BASE=<sha> HEAD=<sha>` |
| `security-sast` | `make sast` (Semgrep); Ruff's security (`S`) rules run in `make lint-api` |

Pull requests run only the jobs their changed files can affect (API jobs for
`apps/api/` and the PgBouncer image, web jobs for the web app, its packages and the Node toolchain
files; the `e2e` job for changes to either app, its packages, `e2e/`, the Compose
and Docker files, the Node toolchain files or `.env.example`; all of them for
`Makefile` and workflow changes; none for documentation).
The `api-client` drift check runs whenever the API or web jobs run. The three
security jobs run on every pull request and push, documentation included.
`ci-required` is the one required check: it reports on every pull request and
fails if any job that ran failed or was cancelled, or if a security job was skipped.

### Security scans

```bash
make security                # audit-deps, scan-secrets and sast, as in CI (needs Docker, uv, Node 24)
make scan-image IMAGE=<ref>  # Trivy scan of a built image; fails on fixable CRITICAL findings
```

Scanner versions are pinned in the `Makefile`. When a scan fails, fix the
cause (upgrade the dependency, remove the secret, change the code). If that is
not possible, add an exception with a reason and a review date as described in
[ADR-0016](_docs/adr/0016-ci-security-tooling.md); it is reviewed in the pull
request.

## Documentation

- [Product specification](_docs/spec.md)
- [Architecture](_docs/architecture.md)
- [Design system](_docs/design-system.md) (tokens, theming, accessibility rules)
- [API conventions](_docs/api.md) (paths, errors, pagination, versions, client workflow)
- [Security headers](_docs/security-headers.md) (Content Security Policy, API headers, `Origin` and CSRF)
- [Testing guidelines](_docs/testing-guidelines.md) (test layers, fixtures, permission matrix, financial invariants, E2E, accessibility)
- [Agent instructions](AGENTS.md)
- [Delivery process](_docs/process.md) (agent workflow and contribution guidance)
