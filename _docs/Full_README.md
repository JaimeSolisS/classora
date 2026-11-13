# Classora: full README

The complete developer reference: every command, setting and environment
detail. For a short overview, see the [root README](../README.md).

Classora is a management platform for a single tutoring center. It covers class
scheduling and enrollment, attendance, academic records, billing and
payments, tutor payroll and operational reporting for owners, administrators,
coordinators, finance staff, tutors, students and guardians. It is built as a
FastAPI modular monolith with a React single-page client, backed by PostgreSQL.

## At a glance

```mermaid
flowchart LR
  staff["🏢 <b>Staff</b><br/>Owner · Administrator<br/>Coordinator · Finance"]
  tutors["👩‍🏫 <b>Tutors</b>"]
  families["👪 <b>Families</b><br/>Students · Guardians"]

  app(["📚 <b>Classora</b><br/>runs one tutoring center"])

  classes["📅 <b>Classes</b><br/>individual · group · course"]
  learning["📝 <b>Learning</b><br/>attendance · notes<br/>homework · progress"]
  money["💳 <b>Money</b><br/>family billing and receipts<br/>tutor payroll · expenses"]
  reports["📊 <b>Reports</b><br/>dashboards · KPIs · exports"]

  staff -->|"schedule classes<br/>bill families · pay tutors"| app
  tutors -->|"teach classes<br/>take attendance · write notes"| app
  families -->|"see the schedule<br/>request classes · see progress"| app

  app --> classes
  app --> learning
  app --> money
  app --> reports
```

## Status

**Pre-alpha.** The features below run on a local development stack. A DEV
environment runs on AWS (one EC2 host with Caddy HTTPS and the Compose stacks
under systemd, created with Terraform), every merge to `develop` deploys
to it automatically, its database is backed up nightly, and it is monitored,
with e-mail alerts. A PROD environment exists on the same design; it deploys
only from a release approved by the owner, which promotes the images DEV
tested. The stack is a FastAPI API with
liveness and readiness health checks, a PostgreSQL database reached through
PgBouncer (schema managed with Alembic migrations), a background worker for
queued and periodic jobs (Procrastinate), server-rendered PDF documents
(Jinja2 and WeasyPrint) and Excel files (openpyxl) with es/en server message
catalogs, and a React + TypeScript + Vite web app. The API and worker can send
OpenTelemetry traces, metrics and logs, which stays off unless the optional
observability profile is on. The web app has a routed
shell (layout with a collapsible side navigation, a 404 page and
central error handling), a TanStack Query data layer, a typed API client
generated from the API's OpenAPI document, a design system foundation
(central design tokens, Tailwind CSS, shadcn/ui components catalogued in
Storybook), FullCalendar for the staff calendar, Recharts for progress and
KPI charts, and dnd-kit for reordering dashboard widgets. Every role's home
page shows today's agenda and the pending items they can act on; staff also
get the KPI dashboard below.

Environments: DEV at https://dev.classora.dev, PROD at https://app.classora.dev
(serves only after the first approved release) and monitoring at
https://grafana.classora.dev (login required).

Hardening before go-live (milestone M10) is under way. Every route a tutor,
student or guardian can call with a record id has a cross-party (IDOR) test
named in a CI-enforced inventory, and every background job is classified by
how it checks its eventual reader. A security review found no critical or high
findings ([security review](security-review.md)), and the hosts' size was
load-tested on DEV ([load-test report](load-test-report.md)). Still to do
before go-live: the PROD restore drill, the accessibility audit, the legal
review of retention and privacy texts, and the go-live checklist.

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
  rate-limited, and every client is limited to a number of API requests per
  minute.
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
  Preparatoria, Universidad) and a built-in "General" subject in its own
  "General" level, listed first. General can be renamed but not
  deactivated, any active tutor may teach it without a qualification, and
  it is priced like any other subject.
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
- On the Payments and receipts tab, staff switch payment methods on or off
  (with a password check; at least one stays active) and set a message
  printed on new receipts in each language; receipts already issued don't
  change. Academic periods, prices, billing settings and packages are linked
  from Settings.

**Tutors**

- On the Tutors page, staff and academic coordinators see every tutor's
  profile: the account's contact details and professional experience. Staff
  edit it; a signed-in tutor sees and edits only their own contact details
  and experience.
- Qualifications: tutors request to teach a subject (its education level
  follows from it) and can withdraw a pending request; staff approve or
  reject it, with a reason, on the Approvals page, and can later revoke an
  approved qualification with a reason the tutor sees. Only approved
  qualifications count as the tutor's teaching subjects. Owners and
  Administrators can also assign a subject to a tutor directly from the
  tutor's page; the Tutors list shows each tutor's subjects.
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
  class type. Each session shows its state and its tutor, is coloured by
  class type (individual, group or course, with an icon and a legend), and
  is marked when it falls on a center closure, its tutor is no longer
  qualified for the subject, or it was booked or moved with an override. The day and week views show the
  center's opening hours, shaded where it is closed, and widen to show any
  class outside them.
- Tutors, students and guardians see their own schedules under My schedule,
  as a phone-friendly list or a calendar (with the same opening hours and
  class type colours), and guardians choose a child.
  Families see only confirmed and completed classes, and cancelled ones they
  had seen confirmed.
- Owners and Administrators reschedule a session on the calendar by dragging
  it (or resizing its end), or with the keyboard from its "Reschedule"
  dialog; the server checks every move, refused moves snap back, and soft
  conflicts can be overridden with a reason.
- Staff schedule one-off individual sessions from the calendar's "Schedule a
  session" page (one student, a subject, an optional tutor and classroom, a
  date and times in the center's time zone). The General subject is
  preselected there, on New group and on a new course template; staff can
  choose another. On the same page they can
  instead add a one-off session to an active group or a current course: the
  students enrolled in it take part. Owners and Administrators can
  also open that page from the calendar by dragging over free times (or
  clicking one, for a one-hour session) in Day or Week view: it opens
  pre-filled with the date, the times, and the tutor and room the calendar
  is filtered by. Before anything is saved the
  server checks student, tutor and classroom overlaps, tutor availability and
  qualification, classroom availability, center closures, operating hours
  and booking notice, and lists every conflict. An Owner, or an
  Administrator with the delegated override permission, can override soft
  conflicts with a reason, which is audited, for one-off sessions and
  recurring series (occurrence by occurrence); overlaps and room capacity
  are never overridable.
- From a cancelled class that allows a makeup, staff open the same page to
  schedule the makeup, inside the policy's window, and the two sessions link
  to each other.
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
  session. The "Schedule a session" page lists suggested tutors as soon as
  the subject and time are set, for one-off sessions and weekly series, with
  the reasons (workload, limits, preferred room) and why none fit; once a
  tutor is chosen, it shows their weekly availability and the nearest free
  times.
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
  filtered by status, type, assignee and priority and sorted by priority,
  oldest or newest first. It holds account requests, tutor
  qualification requests, sessions to reassign after a tutor declined or
  didn't answer an offer, families' session requests (reviewed on their
  own page), families' and tutors' cancellation and reschedule requests
  (also reviewed on their own page), tutor availability changes that
  affect scheduled classes, tutors' session closeouts (reviewed on the
  session page), progress reports (reviewed on their own page) and payroll
  periods (decided on their own page). Staff open each request's details
  panel (also from a link or a notification), assign or reassign it (the
  new assignee is notified), and approve or reject it there or follow it to
  its own page (session closeouts and progress reports are approved or sent
  back for changes on their own page, never rejected). The whole inbox works
  with the keyboard and a screen reader. Approving an account request never
  enrolls anyone.

**Billing**

- Owners, administrators and finance staff keep a price catalog (Billing →
  Prices) by subject, student level, duration and class type (individual,
  group or course), optionally for one cohort or group. Each price changes
  from a date: a change is scheduled ahead (or marks the service as not
  offered), an upcoming change can be deleted, and existing prices are
  never edited.
- They can turn billing on from a start date, or off again (Billing →
  Settings). While it is on, confirmed and completed individual and group
  sessions from that date are charged automatically to the student's
  family, as are course fees and flat group fees when a student enrolls
  (prorated by the sessions left for a late enrollment); withdrawing a
  student from a group charged per session credits the future sessions.
  Billing → Unbilled lists the sessions, the group or course enrollments
  and the tuition periods that can't be charged yet (no family, no price
  or a family change) with a link to fix the cause.
- They can set weekly, biweekly or monthly tuition plans (Billing →
  Tuition plans), on their own or for a group enrollment, that charge each
  period automatically from a per-period price, with pause, resume and an
  end date.
- They can sell session or hour packages with an expiry date (Billing →
  Packages), charged to the family when sold. Approved individual sessions
  use them automatically, earliest-expiring first, and unused units
  expire; each package shows its remaining units and history.
- They also keep discounts, scholarships, sibling discounts and promotions
  with eligibility rules and validity dates (Billing → Discounts), and
  custom prices per student or family (Billing → Custom prices); posted
  charges keep the price and discounts they were computed with.
- They open a family's billing account (Billing → Accounts, or from the
  family's page) to see its balances overall and per child, its charges
  and its history, and record cash, transfer or other payments, applied
  oldest first or to chosen charges; whatever isn't applied stays as
  account credit, which they can apply to charges later.
- Each recorded payment gets a numbered, non-tax PDF receipt (Spanish or
  English, in the family's language) that staff download from the family's
  account page.
- Billing → Overdue lists the families with overdue charges, with aging
  buckets (1–30, 31–60, 61–90 and over 90 days). On a family's account,
  staff can change an open charge's due date (with a reason, kept in its
  history) and set extra days to pay for the family's new charges.
- While billing is on, families receive in-app reminders before and after
  their charges fall due (a few days before, once overdue, and again two
  weeks later), sent once per charge and stage to the family's guardians.
- Cancellations, closures and changes to a class's length correct the
  family's charge automatically. A rule per cancellation type (on time,
  late, no-show) decides whether a cancelled class is credited or still
  charged; it changes from a date and is edited under Billing → Settings.
- Staff add manual charges and correct posted charges with credits or
  debits (with a reason), and cancel a package purchase (Billing →
  Packages); an Owner, or an administrator they delegate, reverses
  payments and refunds account credit. Corrections ask for a password
  confirmation (except a manual charge) and never change the original
  records.
- Administrators withdraw a student from a group, a course or a prepaid
  package with a settlement preview (the sessions or hours used and
  unused, and what each fee, tuition period or package is credited); the
  credit stays on the family's account or the freed payments are
  refunded, after a password confirmation. The family's account page
  lists its withdrawals.
- Guardians open Family billing (Cuenta familiar) to see their family's
  balance overall and per child, the charges with their due dates and
  status, the payments with downloadable receipts, the prepaid packages
  with what is left, and the account history, on the phone, in Spanish
  and English; a payment reminder opens it. They never see staff notes or
  reasons.

**Payroll**

- Finance staff and owners set each tutor's pay frequency, salary and rate
  history per class type (Payroll → Tutors), and the payroll start date,
  biweekly anchor and salary proration basis (Payroll → Settings). Tutors
  read their own terms and rates under My pay.
- Finance staff and owners version what a late cancellation, a no-show and a
  makeup pay (Payroll → Pay rules), and see which approved work can't be paid
  and why, with a link to the fix (Payroll → Unresolved work).
- Finance staff and owners define bonus and deduction types (Payroll → Bonus
  and deduction types) and add one-time or recurring items on each tutor's pay
  page, which shows where each one lands.
- Finance staff review and prepare each tutor's pay period, line by line
  (Payroll → Periods). The owner or a delegated administrator finalizes it
  with their password or sends it back with a reason, and a finalized period
  can't change.
- Corrections after lock land in a later period as labelled adjustments.
  Finance staff and owners can post a manual adjustment with their password
  (on a finalized line, on unresolved work paid by hand, or free), and move a
  negative period's balance to the next period.
- Finance staff and owners record a tutor's external payment (transfer, cash)
  on a finalized period, partial or full, with its reference as proof, and
  see what's paid and what's left (a paid-state badge and filter on the
  period list). The owner reverses a mistaken payment with their password.
- Tutors see their finalized earnings period by period under My pay
  (classes, hours, rates, salary, bonuses, deductions, adjustments and
  payments) and download each period's payslip as a PDF, which isn't a tax
  document.

**Expenses**

- Owners, administrators and finance staff manage expense categories (five
  defaults that can be renamed and deactivated) and vendors (Expenses →
  Categories, Vendors). A deactivated category or vendor can't be chosen for
  new expenses but stays in past records.
- Owners and finance staff record one-time expenses with a receipt or
  invoice number and a note (no file), filter them by status, category,
  vendor and date, log full or partial payments, correct an amount with an
  adjustment or void a wrong expense and enter it again, and the owner
  reverses a mistaken payment (Expenses → Expenses).
- Owners and finance staff set up weekly, monthly or annual recurring
  expenses that are created automatically each period, see the expenses each
  one generated, and edit, pause, resume or end them; changes apply only to
  expenses not generated yet (Expenses → Recurring).

**Reports**

- Owners and finance staff see revenue, direct tutor cost and gross profit
  by individual tutoring, group tutoring and courses, operating expenses by
  category and vendor, and the net result, on an accrual or cash basis for
  any range up to a year, plus today's receivables, payables and deferred
  revenue (Reports → Financial report).
- KPIs: revenue and result per month, unpaid balances and payroll, course
  profitability, room utilization, student retention and tutor performance,
  each for the roles that see its data (Reports → KPIs).
- Customizable dashboard: staff land on a home dashboard with a fixed
  "Today" panel (today's classes and pending items) above
  the KPI widgets their role may see; each user shows, hides and reorders
  the widgets (mouse, keyboard or buttons), saved per user. Tutors,
  students and guardians get a home with their own classes and pending
  items (offers, closeouts, homework, balance due). Every agenda row shows
  the class type (colour, icon and name) and the tutor.

**Imports**

- CSV import of students, tutors, recurring schedules and opening balances
  with a validation report and an explicit commit: staff, academic
  coordinators (students only) and finance staff (opening balances only)
  choose a CSV saved as UTF-8 (or download a template), review each row's
  errors, warnings and duplicates, then import the valid rows in the
  background and follow the result; past imports are listed with their
  outcome (Imports). Imported tutors get pending invitations, whose links
  come from the Invitations page. Each imported schedule row creates a weekly
  series and passes the same conflict checks as a manual booking (a row with
  a conflict isn't created); each opening balance becomes a pending manual
  charge on the family's account and is never posted twice. Payments and
  credit balances aren't imported. The file itself is never stored.

**Exports**

- CSV and Excel exports of students, class sessions and expenses: staff
  choose the columns, their order, an optional grouping and the filters (a
  date range for sessions and expenses), and the file is built in the
  background from the same list they can see (Exports). Exporting students
  or sessions asks for the password again. Each file is kept in the database
  for 24 hours, only its requester can download it, and a notification says
  when it's ready; nothing goes to file storage. Staff can save an export
  setup under a name and share it with one staff role; running a saved setup
  checks the runner's own access, as a new export does.

**Notifications**

- A bell in the header shows how many notifications are unread and opens a
  panel to read them and mark one or all as read.
- New notifications arrive live (Server-Sent Events); when the live
  connection isn't available, the bell checks every 30 seconds instead.
- Each user turns optional notification types on or off from their account
  menu (Notifications); notices about changes to their own classes can't be
  turned off.
- Today they tell tutors about qualification decisions and revocations and
  new session offers, tell staff when an offer is declined or expires, and
  tell tutors and families about substitutions and cancelled sessions, and
  remind families of charges that are due soon or overdue. In
  local and dev, signed-in users can create a test notification for
  themselves.

**Audit and security**

- Changes to records and security events (sign-ins, passwords,
  invitations) are recorded in an audit log. Owners and Administrators
  browse it on a read-only Audit log page, filtered by record, person and
  date, with each change's before and after values and people shown by
  name. Sensitive pay and expense values stay hidden for every reader.
- Owners and Administrators see failed background jobs on a Failed jobs
  page, with the task, the attempts and a safe error label (never personal
  data), and can retry one. Retries are audited and never duplicate charges
  or records.
- Deleted notes, evaluation and learning plan drafts, homework and progress
  reports can be restored by the person who deleted them for 30 days from a
  Recently deleted page. A daily retention job then removes them for good,
  along with old finished background jobs; financial, payroll, expense,
  attendance, audit and consent records are never deleted.
- Requests that change data are protected by an `Origin` check and a CSRF
  token, and the web app is served with a Content Security Policy and other
  security headers.

**App experience**

- Spanish and English UI text with a language switch, and a light, dark or
  system theme. Signed-in users' language and theme are saved to their
  account and applied when they sign in on any device.
- Pages and components are checked automatically for accessibility (axe) in
  light and dark themes.

**Not built yet:** the rest of billing and PDF exports. See the [milestones](https://github.com/JaimeSolisS/classora/milestones) and
[`_docs/tasks.md`](tasks.md).

**Not in this version:** file uploads and documents (attachments, the shared
academic library, malware scanning, file previews and the recycle bin) are
planned for a later version.

## Prerequisites

### Docker-only path

To run the app, load the simulated data and run the unit and integration
tests, you need only:

- [Docker Engine](https://docs.docker.com/engine/) 20.10 or later, or Docker
  Desktop, with Docker Compose v2
- GNU Make (and the POSIX shell and bash that macOS and Linux ship with)

`make start`, `make seed-data-spanish` / `make seed-data-english` and
`make test` run everything in containers: Python, Node.js, PostgreSQL and the
PDF libraries come from the images (both `linux/amd64` and `linux/arm64`).
`make test` reaches the Docker daemon through its socket
(`/var/run/docker.sock`) for its throwaway databases, so it does not work with
rootless Docker, Podman, or Docker Desktop with Enhanced Container Isolation,
nor on a Linux host whose firewall (ufw, firewalld) blocks containers from
reaching the host's published ports. Use the native path there.
`make e2e`, the linters and Storybook need the native tools below.

### Native development

The engineer workflow (the `-native` targets, `make test-native` and its
per-layer targets, linting, `make e2e`) also needs:

- [uv](https://docs.astral.sh/uv/) (installs the pinned Python version; no other Python install is needed)
- [Node.js](https://nodejs.org/) 24 (see `.nvmrc`; with nvm, run `nvm use`). pnpm
  runs through Corepack, which ships with Node 24, so no global pnpm install is
  needed. Other Node majors are rejected with an engine error.
- Pango, for server-rendered PDFs (receipts). macOS: `brew install pango`; on
  Apple Silicon also export `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
  Debian/Ubuntu: `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
  fonts-dejavu-core`. The Docker images already include it.

## Getting started

```bash
make start
```

`make start` runs the three first-run steps in order:

```bash
cp .env.example .env   # only if .env does not exist yet
make migrate
make dev
```

`make migrate` creates the database and applies the migrations (see
[Database migrations](#database-migrations)); on a new checkout or volume run it
**before** `make dev`. `make dev` builds the images and starts PostgreSQL,
PgBouncer, the API, the background worker and the web app in the background,
and returns once all of them are healthy. The API reaches PostgreSQL through
PgBouncer (connection pooling). The API and the worker connect as a
least-privilege database role (`classora_app`: data access only, no schema
changes), while migrations, seeds and operator commands use the database owner.
Then open:

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
queued and periodic jobs. Follow its logs with `docker compose logs -f worker`. In local
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
  background worker (`worker`) and the web app (`web`), plus the `migrate`
  tools service (Compose profile `tools`: never started by `make dev`;
  `make migrate`, `make seed` and `make bootstrap-owner` run it once with
  `docker compose run --rm`).

Database roles: `api`, `worker` and `pgbouncer` get only the runtime role
`classora_app` (password `POSTGRES_APP_PASSWORD`); `db` and `migrate` get the
owner (`POSTGRES_USER`/`POSTGRES_PASSWORD`). PgBouncer knows only the runtime
role.

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

### Observability (optional)

An optional monitoring stack runs next to the app: an OpenTelemetry Collector,
Prometheus, Loki, Tempo and Grafana, from the same configuration files the AWS
monitoring host uses (`infrastructure/observability/`,
[ADR-0014](adr/0014-observability-error-tracking-and-alerting.md)). It is
**off by default**: `make dev` never starts it, and CI only validates its
configuration (`make check-observability`).

```bash
make dev-observability        # the app stack plus the five observability services
make dev-observability-down   # stops only those five; V=1 also deletes their data
```

- It needs about **700 MB more RAM** (each service has a hard memory limit).
  On an 8 GB machine, run it alone, not next to test suites or builds.
- Grafana: <http://127.0.0.1:3000> (`GRAFANA_PORT`, `127.0.0.1` only). Sign in
  as `admin` with `GRAFANA_ADMIN_PASSWORD` from `.env`; there is no anonymous
  access. Grafana keeps the password from its first start: to change it, run
  `make dev-observability-down V=1` first.
- The "Host and containers" dashboard shows per-container CPU and memory,
  PostgreSQL connections, the API's readiness and the background jobs by status
  (after `make migrate`). Data appears within about two minutes.
- The API and worker send traces, metrics and logs only while the profile is on
  (`make dev-observability` sets `OTEL_EXPORTER_OTLP_ENDPOINT`; `make dev` leaves
  telemetry off). The API, Database, Jobs, Errors and Logs dashboards and the
  alert rules come from the same files.
- Metrics are kept 15 days, logs and traces 7 days (in Docker volumes).

### Database migrations

```bash
make migrate
```

`make migrate` applies the Alembic migrations to the local Compose database
(it starts the database if it is not running), then gives the runtime role
`classora_app` its login and the `POSTGRES_APP_PASSWORD` password
(`python -m app.cli sync-runtime-role`). It runs in the `migrate` tools service
as the database owner (`DATABASE_MIGRATION_URL`), directly, not through
PgBouncer. Neither `make dev` nor the API runs migrations. On a new checkout or
volume run `make migrate` **before** `make dev` (PgBouncer and the worker log
in as `classora_app`, which exists only after it; `make dev` prints this hint
when it fails), and again whenever you pull new migrations. Running it when the
database is already up to date changes nothing.

**Upgrading an existing `.env` (before the runtime role):** add
`POSTGRES_APP_PASSWORD` and `DATABASE_MIGRATION_URL` from `.env.example`, set
the user and password of `DATABASE_URL` and `DATABASE_DIRECT_URL` to
`classora_app` and `POSTGRES_APP_PASSWORD` (as in `.env.example`), and keep each
key once. Then run `make migrate` and `make dev`. The existing volume and its
data are kept.

### Seed data

```bash
make seed                # reference data
make seed-data-spanish   # a simulated term of a tutoring center, in Spanish (local and DEV only)
make seed-data-english   # the same simulated term, in English (local and DEV only)
make seed-data-spanish-native   # the same, on the host into the database .env names (native stack)
make seed-data-english-native
```

`make seed` applies the reference data every environment needs: the user
roles and their permissions. Run `make migrate` first: it refuses to run on a
database with pending migrations. It is safe to repeat; a second run changes
nothing.

The simulated seeds are the local demo data: `make seed-data-spanish` or
`make seed-data-english` fills a database with a term's worth of realistic,
synthetic data and accounts for every role (see
[Simulated operations data](#simulated-operations-data)). They run only with
`APP_ENV=local` or `dev` (DEV gets the English center from its deploy
pipeline, see below; PROD never does), and each language needs a **freshly migrated
database** of its own. They refuse a database that already has an account,
and `make bootstrap-owner` (below) is refused once any Owner exists, so use
either the simulated data or `make bootstrap-owner` on a database, not both.

#### Simulated operations data

```bash
make seed-data-spanish   # a simulated tutoring center, in Spanish
make seed-data-english   # the same center, in English (its own database)
```

These commands generate an invented tutoring center that has been running
for a term, so you can see the system with realistic volumes. Both write
through the application's services, and the names, addresses and texts are
synthetic. They refuse every `APP_ENV` except `local` and `dev` (`prod`
exits 2 before touching anything). Both need `.env` (`make start` creates it).

- `make seed-data-spanish` / `make seed-data-english` run in the Compose tools
  service `devtools` (the API image's `test` stage, which has the development
  dependencies) against the Compose database, as its owner, like `make seed`.
  They need only Docker; run `make start` first. The first run builds the
  image (`--build`; later runs reuse it).
- `make seed-data-spanish-native` / `make seed-data-english-native` run the
  same command on the host (with the API's development dependencies, via
  `uv`) against the database `.env` names: the native stack.

They need a **freshly migrated database** (the reference data and nothing
else). They refuse a database that already has any account, student, class,
charge, expense or payroll, or billing or payroll configured, and say how to
reset. Running the same command again changes
nothing. The other language refuses that database, so give each language
its own:

```bash
# native stack
make db-shared && make db-create NAME=classora_sim_es   # or classora_sim_en
# set DATABASE_URL, DATABASE_DIRECT_URL and DATABASE_MIGRATION_URL in .env to the printed URLs
make api-native          # migrates, then serves; keep it running
make seed-data-spanish-native   # in another terminal

# Compose
docker compose down -v && make start && make seed-data-spanish
```

Then open <http://127.0.0.1:5173> (native stack: start `make web-native`
too) and sign in as the Owner (`direccion` in
Spanish, `owner` in English, password `classora-sim-owner`); every other
login is listed below. To keep both languages, point
the three database URLs in `.env` at the other language's
database and restart `make api-native` to switch.

The command prints the reference-seed lines and then one `<module>: <n>
rows` line per part of the simulation. It writes everything in one
transaction, so a failed run leaves the database as it was. It exits with:

| Exit code | Meaning |
|---|---|
| `0` | Data generated, or already there in this language (nothing changed) |
| `1` | Configuration error, or the database has pending migrations (run `make migrate`) |
| `2` | `APP_ENV` is not `local` or `dev`; nothing is touched |
| `3` | The database is not fresh, or holds the other language; the message says why, and nothing is written |

The timeline is fixed, whatever day you run it. History runs from
1 Sep to 18 Nov 2026. From 19 Nov 2026 to 5 Dec 2026, items are only
scheduled or pending.

**On DEV** (<https://dev.classora.dev>) the database holds the English
center (`--seed 1`) plus one more Owner for visitors and graders:
`visitor` / `visitor-classora` (display name "Demo visitor"). Every English
account below also works there with its published password. DEV is wiped
and restored to this data every night at 04:00 UTC, so changes made there
last until then. The pipeline and the reset are in the
[DEV data runbook](runbooks/dev-data.md). PROD never gets simulated data or
the visitor.

The staff accounts use development passwords. Tutors, guardians and
students who can sign in use one password per role. The lists below are
exactly the logins each command creates (with its default seed), and every
one of them is active.

| Spanish username | English username | Password | Role |
|---|---|---|---|
| `direccion` | `owner` | `classora-sim-owner` | Owner |
| `administracion` | `admin` | `classora-sim-admin` | Administrator |
| `coordinacion` | `coordinator` | `classora-sim-coordinator` | Academic coordinator |
| `finanzas` | `finance` | `classora-sim-finance` | Finance staff |

<details>
<summary>Spanish accounts (<code>make seed-data-spanish</code>): 12 tutors, 30 guardians, 6 students</summary>

<!-- sim-accounts:es:start -->
| Username | Name | Role | Family | Password |
|---|---|---|---|---|
| `gustavo.acevedo` | Gustavo Acevedo Ramírez | Tutor | — | `classora-sim-tutor` |
| `camilo.giron` | Camilo Girón Villareal | Tutor | — | `classora-sim-tutor` |
| `aldonza.angulo` | Aldonza Angulo Alonzo | Tutor | — | `classora-sim-tutor` |
| `juan.toro` | Juan Toro Aguirre | Tutor | — | `classora-sim-tutor` |
| `aldo.galvez` | Aldo Gálvez Pichardo | Tutor | — | `classora-sim-tutor` |
| `oswaldo.garibay` | Oswaldo Garibay Quezada | Tutor | — | `classora-sim-tutor` |
| `gabriel.gonzales` | Gabriel Gonzales Montaño | Tutor | — | `classora-sim-tutor` |
| `humberto.gaona` | Humberto Gaona Rico | Tutor | — | `classora-sim-tutor` |
| `sara.pena` | Sara Peña Valadez | Tutor | — | `classora-sim-tutor` |
| `bianca.escamilla` | Bianca Escamilla Lucio | Tutor | — | `classora-sim-tutor` |
| `manuel.mercado` | Manuel Mercado Sanches | Tutor | — | `classora-sim-tutor` |
| `horacio.espinal` | Horacio Espinal Madrigal | Tutor | — | `classora-sim-tutor` |
| `patricia.sanabria` | Patricia Sanabria Santiago | Parent or guardian | Familia Villagómez Sanabria | `classora-sim-guardian` |
| `pamela.armas` | Pamela Armas Manzanares | Parent or guardian | Familia Rodarte Armas | `classora-sim-guardian` |
| `minerva.olivera` | Minerva Olivera Sepúlveda | Parent or guardian | Familia Adame Olivera | `classora-sim-guardian` |
| `maria.tijerina` | María Cristina Tijerina Toledo | Parent or guardian | Familia Abreu Tijerina | `classora-sim-guardian` |
| `margarita.batista` | Margarita Batista Botello | Parent or guardian | Familia Apodaca Batista | `classora-sim-guardian` |
| `maria.renteria` | María José Rentería Medina | Parent or guardian | Familia Lozano Rentería | `classora-sim-guardian` |
| `alta.almanza` | Alta  Gracia Almanza Osorio | Parent or guardian | Familia Garza Almanza | `classora-sim-guardian` |
| `guillermina.angulo` | Guillermina Angulo Ortega | Parent or guardian | Familia Soria Angulo | `classora-sim-guardian` |
| `maria.villanueva` | María Cristina Villanueva Gamboa | Parent or guardian | Familia Archuleta Villanueva | `classora-sim-guardian` |
| `aida.lucio` | Aida Lucio Santana | Parent or guardian | Familia Orellana Lucio | `classora-sim-guardian` |
| `tania.portillo` | Tania Portillo Gallardo | Parent or guardian | Familia Mojica Portillo | `classora-sim-guardian` |
| `antonia.gollum` | Antonia Gollum Barragán | Parent or guardian | Familia Tejada Gollum | `classora-sim-guardian` |
| `berta.nazario` | Berta Nazario Mireles | Parent or guardian | Familia Herrera Nazario | `classora-sim-guardian` |
| `leticia.cano` | Leticia Cano Mayorga | Parent or guardian | Familia Ureña Cano | `classora-sim-guardian` |
| `nayeli.ochoa` | Nayeli Ochoa Bermúdez | Parent or guardian | Familia Villagómez Ochoa | `classora-sim-guardian` |
| `esther.tellez` | Esther Téllez Carrero | Parent or guardian | Familia Valle Téllez | `classora-sim-guardian` |
| `elvia.carrero` | Elvia Carrero Arenas | Parent or guardian | Familia Pedraza Carrero | `classora-sim-guardian` |
| `luisa.guardado` | Luisa Guardado Covarrubias | Parent or guardian | Familia de la Fuente Guardado | `classora-sim-guardian` |
| `abraham.franco` | Abraham Franco Amador | Parent or guardian | Familia Franco Matías | `classora-sim-guardian` |
| `cesar.barela` | César Barela Reséndez | Parent or guardian | Familia Barela Mateo | `classora-sim-guardian` |
| `emilia.franco` | Emilia Franco Maldonado | Parent or guardian | Familia Toro Franco | `classora-sim-guardian` |
| `julia.velasco` | Julia Velasco Mojica | Parent or guardian | Familia Bustamante Velasco | `classora-sim-guardian` |
| `asuncion.madrigal` | Asunción Madrigal Guillen | Parent or guardian | Familia Véliz Madrigal | `classora-sim-guardian` |
| `laura.garay` | Laura Garay Almonte | Parent or guardian | Familia Guevara Garay | `classora-sim-guardian` |
| `oswaldo.alonzo` | Oswaldo Alonzo Muñoz | Parent or guardian | Familia Alonzo Alarcón | `classora-sim-guardian` |
| `catalina.saucedo` | Catalina Saucedo Delgadillo | Parent or guardian | Familia Bermúdez Saucedo | `classora-sim-guardian` |
| `gilberto.zarate` | Gilberto Zarate Lozada | Parent or guardian | Familia Zarate Lozada | `classora-sim-guardian` |
| `jorge.florez` | Jorge Luis Flórez Domínguez | Parent or guardian | Familia Flórez Domínguez | `classora-sim-guardian` |
| `evelio.barela` | Evelio Barela Ramón | Parent or guardian | Familia Barela Ramón | `classora-sim-guardian` |
| `fernando.alba` | Fernando Alba Ortiz | Parent or guardian | Familia Alba Ortiz | `classora-sim-guardian` |
| `gabriel.rodarte` | Gabriel Rodarte Armas | Student | Familia Rodarte Armas | `classora-sim-student` |
| `delia.adame` | Delia Adame Olivera | Student | Familia Adame Olivera | `classora-sim-student` |
| `carmen.zarate` | Carmen Zarate Lozada | Student | Familia Zarate Lozada | `classora-sim-student` |
| `rene.florez` | René Flórez Domínguez | Student | Familia Flórez Domínguez | `classora-sim-student` |
| `esmeralda.barela` | Esmeralda Barela Ramón | Student | Familia Barela Ramón | `classora-sim-student` |
| `esperanza.alba` | Esperanza Alba Ortiz | Student | Familia Alba Ortiz | `classora-sim-student` |
<!-- sim-accounts:es:end -->

</details>

<details>
<summary>English accounts (<code>make seed-data-english</code>): 12 tutors, 30 guardians, 6 students</summary>

<!-- sim-accounts:en:start -->
| Username | Name | Role | Family | Password |
|---|---|---|---|---|
| `pamela.reynolds` | Pamela Reynolds | Tutor | — | `classora-sim-tutor` |
| `deborah.dominguez` | Deborah Dominguez | Tutor | — | `classora-sim-tutor` |
| `ashley.garza` | Ashley Garza | Tutor | — | `classora-sim-tutor` |
| `allison.johnson` | Allison Johnson | Tutor | — | `classora-sim-tutor` |
| `stanley.smith` | Stanley Smith | Tutor | — | `classora-sim-tutor` |
| `javier.robinson` | Javier Robinson | Tutor | — | `classora-sim-tutor` |
| `pamela.rose` | Pamela Rose | Tutor | — | `classora-sim-tutor` |
| `gregory.tran` | Gregory Tran | Tutor | — | `classora-sim-tutor` |
| `joe.robinson` | Joe Robinson | Tutor | — | `classora-sim-tutor` |
| `jennifer.house` | Jennifer House | Tutor | — | `classora-sim-tutor` |
| `brett.nielsen` | Brett Nielsen | Tutor | — | `classora-sim-tutor` |
| `scott.riddle` | Scott Riddle | Tutor | — | `classora-sim-tutor` |
| `faith.martinez` | Faith Martinez | Parent or guardian | The Martinez family | `classora-sim-guardian` |
| `kimberly.thompson` | Kimberly Thompson | Parent or guardian | The Thompson family | `classora-sim-guardian` |
| `kathryn.jackson` | Kathryn Jackson | Parent or guardian | The Jackson family | `classora-sim-guardian` |
| `monica.lynch` | Monica Lynch | Parent or guardian | The Lynch family | `classora-sim-guardian` |
| `laura.berg` | Laura Berg | Parent or guardian | The Berg family | `classora-sim-guardian` |
| `krista.reid` | Krista Reid | Parent or guardian | The Reid family | `classora-sim-guardian` |
| `ashley.wright` | Ashley Wright | Parent or guardian | The Wright family | `classora-sim-guardian` |
| `erica.gibson` | Erica Gibson | Parent or guardian | The Gibson family | `classora-sim-guardian` |
| `toni.johnson` | Toni Johnson | Parent or guardian | The Johnson family | `classora-sim-guardian` |
| `mary.lara` | Mary Lara | Parent or guardian | The Lara family | `classora-sim-guardian` |
| `elizabeth.stark` | Elizabeth Stark | Parent or guardian | The Stark family | `classora-sim-guardian` |
| `sabrina.johnson` | Sabrina Johnson | Parent or guardian | The Johnson family | `classora-sim-guardian` |
| `hannah.gray` | Hannah Gray | Parent or guardian | The Gray family | `classora-sim-guardian` |
| `jeanette.hurley` | Jeanette Hurley | Parent or guardian | The Hurley family | `classora-sim-guardian` |
| `christina.smith` | Christina Smith | Parent or guardian | The Smith family | `classora-sim-guardian` |
| `sara.craig` | Sara Craig | Parent or guardian | The Craig family | `classora-sim-guardian` |
| `ana.patel` | Ana Patel | Parent or guardian | The Patel family | `classora-sim-guardian` |
| `olivia.lewis` | Olivia Lewis | Parent or guardian | The Lewis family | `classora-sim-guardian` |
| `mike.chan` | Mike Chan | Parent or guardian | The Chan family | `classora-sim-guardian` |
| `charles.esparza` | Charles Esparza | Parent or guardian | The Esparza family | `classora-sim-guardian` |
| `tiffany.knight` | Tiffany Knight | Parent or guardian | The Knight family | `classora-sim-guardian` |
| `destiny.ellis` | Destiny Ellis | Parent or guardian | The Ellis family | `classora-sim-guardian` |
| `ashley.davis` | Ashley Davis | Parent or guardian | The Davis family | `classora-sim-guardian` |
| `candice.willis` | Candice Willis | Parent or guardian | The Willis family | `classora-sim-guardian` |
| `thomas.garcia` | Thomas Garcia | Parent or guardian | The Garcia family | `classora-sim-guardian` |
| `karen.king` | Karen King | Parent or guardian | The King family | `classora-sim-guardian` |
| `william.buchanan` | William Buchanan | Parent or guardian | The Buchanan family | `classora-sim-guardian` |
| `tyler.edwards` | Tyler Edwards | Parent or guardian | The Edwards family | `classora-sim-guardian` |
| `william.rodriguez` | William Rodriguez | Parent or guardian | The Rodriguez family | `classora-sim-guardian` |
| `ricky.dillon` | Ricky Dillon | Parent or guardian | The Dillon family | `classora-sim-guardian` |
| `john.thompson` | John Thompson | Student | The Thompson family | `classora-sim-student` |
| `patricia.jackson` | Patricia Jackson | Student | The Jackson family | `classora-sim-student` |
| `tammy.buchanan` | Tammy Buchanan | Student | The Buchanan family | `classora-sim-student` |
| `arthur.edwards` | Arthur Edwards | Student | The Edwards family | `classora-sim-student` |
| `cindy.rodriguez` | Cindy Rodriguez | Student | The Rodriguez family | `classora-sim-student` |
| `julie.dillon` | Julie Dillon | Student | The Dillon family | `classora-sim-student` |
<!-- sim-accounts:en:end -->

</details>

Two more people can't sign in yet, so they aren't listed: a pending
self-registration (`camila.chavarria` in Spanish, `ann.wilkins` in English),
which you can approve on Approvals, and a pending tutor invitation
(`hernan.chavarria` / `brandon.mullins`), which has no password.

What's in it:
- **People:** 40 students in 30 families with 46 guardians (each adult
  student's family has the parent who pays), plus two younger siblings
  added on 2 Sep by a students import (42 students). Four students are
  adults, and four enroll during the term. Every family's first guardian
  has a login. Every minor has a guardian consent. There is one approved
  and one pending self-registration, and one pending tutor invitation.
- **Catalog:** 12 subjects across the four levels, five classrooms (one
  closed for renovation on 30 Oct), the 2026–2027 school year and three
  holidays.
- **Tutor profiles:** experience, the subjects each tutor teaches
  (approved qualifications; one rejected, one revoked, one pending), weekly
  availability, limits and breaks, time off
  and preferred classrooms.
- **Center settings:** name, contact details, receipt messages, opening
  hours and the scheduling policy. In English, the education levels and
  expense categories are renamed.
- **Classes:** about 80 classes a week: one to three weekly individual
  classes per student, three class groups and two courses (one that ended
  in October, one running into December), always within the tutors'
  subjects, availability and limits. Before 19 Nov every class is held and
  closed out (attendance, approved closeout), cancelled (on time, late or
  excused), moved by an approved reschedule, covered by a substitute after
  approved time off, or skipped for a holiday; there are make-ups, session
  and change requests (approved and rejected), declined and expired offers,
  and attendance and closeout corrections. From 19 Nov classes are
  scheduled, with pending session and change requests, offers waiting for a
  tutor, unassigned make-ups, a time off and an availability change. See
  them in the calendar, Approvals and each tutor's or family's schedule.
- **Academics:** tutors' class notes (most shared with the family, some
  internal), final evaluations (an initial assessment for each student
  enrolled before October, a monthly evaluation at the end of September
  and of October, and the courses' assessments), learning plans
  (completed, discontinued and active, some with an overdue milestone),
  homework every other week (graded or missing before 19 Nov; pending or
  handed in early after it), and progress reports approved for families
  (September and October monthly reports, two on-demand reports). Two
  weekly and one monthly report schedule are set up, and two weekly
  reports for 9–15 Nov wait for review in the Academic coordinator's
  Approvals and progress reports. See them in each student's record
  (history, learning plans, homework), the homework and progress report
  lists, and the family's own pages.
- **Billing:** billing runs from 1 Sep with catalog prices (one price
  change on 1 Oct), custom prices, a sibling discount, two scholarships, a
  September promotion (expired) and a discount starting 1 Dec. About half
  the students pay monthly plans, one per subject (one student's paused
  for October), a fifth use prepaid class packages (renewed when used up,
  one lapsed with classes left, one cancelled and refunded, one withdrawn
  as account credit) and the rest pay per class. Two groups charge a
  monthly fee through plans linked to each enrollment; the third group and
  the courses charge an enrollment fee. A flat group fee counts on its
  enrollment date (24 Aug), so it isn't in the term's monthly reports.
  Families pay in cash, by transfer or otherwise, with a receipt for each
  payment; most pay on time, some late, two keep account credit, and four
  families have overdue charges, all with reminders, at 19 Nov. There is a
  bounced transfer paid again, manual charges and an adjustment, due-date
  changes, credited cancellations and a repriced class. See them in
  Billing (accounts, overdue accounts, packages, plans, discounts and
  prices) and in each family's billing pages. Receipts are in the run's
  language.
- **Payroll and expenses:** payroll runs from 31 Aug. Ten tutors are paid
  every two weeks (by the hour or per class, groups by the hour, the two
  course tutors a per-course rate) and two get a monthly salary; late
  family cancellations and no-shows pay 50 %. Every pay period that ended
  before 19 Nov is prepared by Finance, finalized by the Owner (with a
  payslip in the run's language) and paid; the fortnight of 9–22 Nov and
  November are open. There are a punctuality bonus, a lost-materials
  deduction, two closeouts approved late and an hours correction deducted in the
  next fortnight, a manual adjustment, the new tutor's pay advance carried
  forward, and a payment reversed and paid again. Expenses come from
  recurring templates (rent, electricity and water, weekly cleaning paused
  for two weeks in October, a software subscription, the annual insurance)
  and one-off purchases and repairs; all are paid (one duplicate voided,
  one transfer reversed and paid again, two adjustments), and one vendor is
  inactive. See them in Payroll, each tutor's earnings and payslips,
  Expenses and the financial reports.
- **Administration:** four imports (Administration > Imports): the
  students file (two children created, one duplicate skipped; one child
  tries two classes in September and doesn't continue), Finance's opening
  balances (two charges, paid), a schedules file in October (one weekly
  series from 23 Nov, one row failed with a scheduling conflict) and a
  tutors file left staged on 18 Nov. In Approvals, the Owner has assigned
  the pending qualification to the Administrator and a progress report to
  the Academic coordinator; the other open items are unassigned. One tutor
  and one guardian have turned off a notification type. Notifications
  older than 12 Nov are read, newer ones unread. The contact email changed
  on 5 Oct, and a scheduling policy version (booking notice 12 h) applies
  from 1 Nov.
- **Reports:** three saved report setups (Finance's "monthly expenses",
  shared with the Owner role; two private students setups), two exports
  (Finance's October expenses, expired; the Owner's November classes,
  ready), customized dashboards for the Owner and Finance (the other roles
  keep the defaults), and in a tutor's Recently deleted, a learning-plan
  draft and an internal note deleted on 12 Nov. No background job has
  failed. The audit log holds every change the seed made.

The data is a snapshot as of 19 Nov 2026, whatever day you run the seed.
The worker works against the real clock, so if it runs:
- pending offers and requests expire, and once the real date passes 18
  Nov the later classes look not held; homework due after 18 Nov shows as
  missing once its deadline passes;
- its progress report schedule adds a draft report each time the real
  date passes the end of a later week or month (only the latest one);
- once the real date passes 1 Dec, December's monthly tuition is posted
  and families are reminded about charges that become overdue (charges and
  balances are always judged against the real date);
- once the real date passes them, the next pay periods open and the
  templates' later expenses are generated (up to 5 Dec, unpaid);
- the Owner's export expires 24 hours after 18 Nov 17:00, the staged
  tutors import 7 days after the seed ran, and the recently deleted
  records are purged 30 days after 12 Nov.

To keep the snapshot, don't start the worker (native stack: skip `make
worker-native`; Compose: `docker compose stop worker`).

KPIs: the dashboard's default range ends at the real today. To see the
simulated term, use a custom range ending 18 Nov 2026 (for example 1 Sep to
18 Nov; before that real date, end it today); for retention, compare
October with September. The term shows a profit in every month; November
looks stronger than September and October until its pay periods are
finalized.

Audit events and notifications show the time the seed ran, not the
simulated date, because the database sets those times.

To start again, give the language a fresh database: native stack `make
db-drop NAME=classora_sim_es && make db-create NAME=classora_sim_es`, then
`make api-native` (migrates) and the target; Compose `docker compose down
-v`, `make migrate`, `make dev` and the target.

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

Open the web app at <http://127.0.0.1:5173> and sign in with a
simulated-data account from [Seed data](#seed-data) (for example the Owner
`direccion` / `classora-sim-owner` from `make seed-data-spanish`, or `owner` /
`classora-sim-owner` from `make seed-data-english`; the other accounts are in
[Simulated operations data](#simulated-operations-data)) or with the account
from `make bootstrap-owner`. The first time an account signs in, the app
shows the privacy notice; accept it to continue. To call the API directly,
log in against it and keep the session cookie, for example with the
simulated Spanish Owner:

```bash
curl -s -c cookies.txt -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:5173' \
  -d '{"username": "direccion", "password": "classora-sim-owner"}' \
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
most) for staff accounts such as the Owner, and after 7 idle days (30 days
at most) for tutors, students and guardians; `/api/v1/auth/me` then answers
`401 session_expired`, so log in again. Details: [`_docs/api.md`](api.md) and
[`_docs/security-headers.md`](security-headers.md).

### Native local stack

For day-to-day work, and for several git worktrees side by side, the API,
worker and web app can run directly on the host against one shared PostgreSQL
container, with no image builds:

```bash
make db-shared                    # start the shared PostgreSQL server (127.0.0.1:55432; idempotent)
make db-create NAME=<name>        # create a database and role <name> ([a-z0-9_]+); prints its three URLs
make api-native                   # migrations, then the API with reload on API_PORT
make worker-native                # the background worker
make web-native                   # Vite with hot reload on WEB_PORT
make seed-data-spanish-native     # simulated data into the same, freshly migrated database (or seed-data-english-native)
make db-drop NAME=<name>          # drop the database and role (classora_app stays)
make db-shared-down               # stop the server (V=1 also deletes its volume)
```

Run `api-native`, `worker-native` and `web-native` in separate terminals (stop
each with Ctrl-C before `make db-drop`). They read the same keys as Compose
from the checkout's `.env` (`ENV_FILE=<path>` picks another file), and a
variable set in the shell wins over the file. Set the three URLs
`make db-create` prints: `DATABASE_URL` and `DATABASE_DIRECT_URL` both to the
runtime role `classora_app` (both direct: there is no PgBouncer), and
`DATABASE_MIGRATION_URL` to the database's own role, which owns it and runs
the migrations and seeds. `make db-create` also creates `classora_app` on the
shared server once. Set `API_PORT`, `WEB_PORT` and `APP_ORIGIN`
(`http://127.0.0.1:<WEB_PORT>`) to ports no other stack uses. The web server
proxies to `API_PROXY_TARGET`, by default `http://127.0.0.1:<API_PORT>`. For
example, two worktrees, each with its own database:

```bash
# worktree A (.env):  API_PORT=8101  WEB_PORT=5274  APP_ORIGIN=http://127.0.0.1:5274
#   DATABASE_URL and DATABASE_DIRECT_URL: postgresql://classora_app:classora_app_local_only@127.0.0.1:55432/wt_a
#   DATABASE_MIGRATION_URL:              postgresql://wt_a:classora_local_only@127.0.0.1:55432/wt_a
# worktree B (.env):  API_PORT=8102  WEB_PORT=5275  APP_ORIGIN=http://127.0.0.1:5275
#   DATABASE_URL and DATABASE_DIRECT_URL: postgresql://classora_app:classora_app_local_only@127.0.0.1:55432/wt_b
#   DATABASE_MIGRATION_URL:              postgresql://wt_b:classora_local_only@127.0.0.1:55432/wt_b
make db-shared && make db-create NAME=wt_a   # in A (B: NAME=wt_b)
make api-native                              # then worker-native, web-native, seed-data-spanish-native
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

There are three database URLs
([ADR-0003 amendment](adr/0003-database-connection-topology.md)):
`DATABASE_URL` is the pooled one (through PgBouncer) and `DATABASE_DIRECT_URL`
the direct one to PostgreSQL (the API's live-notification connection and the
worker); both log in as the least-privilege runtime role `classora_app`.
`DATABASE_MIGRATION_URL` (optional) is a direct URL as the database owner,
read only by migrations, seeds, operator commands and the local data
factories; unset, they use `DATABASE_DIRECT_URL`, and as `classora_app` a
migration fails without changing anything. Inside Compose all three are built
from the `POSTGRES_*` values; the values in `.env` only matter when you run
the API, Alembic or the simulated seeds (`make seed-data-spanish-native`,
`make seed-data-english-native`) on the host.

If your `.env` was copied before a variable was added, refresh it from
`.env.example` (for example, copy it again).

## Running tests

Docker must be running: API integration tests start a throwaway PostgreSQL
container (and a PgBouncer container built from this repository) with
[Testcontainers](https://testcontainers.com/). The Compose
stack and `.env` are not needed.

### In Docker (Docker and Make only)

```bash
make test              # all API tests (unit + integration) and the web and API client tests, in containers
make test-api-docker   # only the API part
make test-web-docker   # only the web and API client part (Vitest)
make test SHARDS=2     # fewer API containers at once, for smaller machines (default 4)
make test-api-docker PYTEST_ARGS="tests/unit/test_pdf.py tests/integration/test_health_ready.py"   # a narrower selection
```

`make test` builds the `test` stages of `infrastructure/docker/api.Dockerfile`
(the API's development dependencies, Pango and the whole repository) and
`infrastructure/docker/web.Dockerfile` (Node 24 and pnpm from `packageManager`),
then runs the API suite as `SHARDS` containers at once, each
`pytest --shard=K/SHARDS` with its own Testcontainers PostgreSQL, next to one
container for the web and API client Vitest suites
(`infrastructure/docker/test-in-docker.sh`). The API containers use the host's
Docker daemon through its socket: their PostgreSQL, PgBouncer and Ryuk
containers are siblings, reached at `host.docker.internal`. It selects the same
tests as `make test-native`. `PYTEST_ARGS` adds pytest arguments (paths are
relative to `apps/api`; a shard that gets no file passes). Every part always
runs; the command prints one summary line per part, then the failure summary
and the last 40 log lines of any part that failed, and exits non-zero if any
part failed. Each part's full output is in `test-results/docker/<part>.log`.
Ctrl-C removes the test containers. There is no coverage report in Docker: the
native targets below (and CI) keep it. With `SHARDS=4` the Docker VM needs
about 4 to 5 GB of memory; use `SHARDS=2` (or `1`) on a machine with less than
16 GB of RAM or a Docker Desktop VM under 8 GB. The unsupported setups are listed
under [Prerequisites](#docker-only-path).

### Native (engineers)

These run on the host and need the [native tools](#native-development); the
API unit tests render PDFs, so they need Pango.

```bash
make test-native       # all tests: API (unit + integration) then web (Vitest)
make test-api          # all API tests with a coverage report
make test-web          # web and API client tests (Vitest, no Docker needed; includes the es/en translation-key check)
make test-unit         # API unit tests only (no Docker needed)
make test-integration  # API integration tests except the migration checks, the billing invariant suite and the simulation seed tests (SHARD=K/N runs one CI shard)
make test-billing-invariants  # billing invariant suite: randomized sequences, races, job walks (SEEDS=, STEPS=, EXTRA_SEED= reproduce a failure)
make test-simulation   # simulated-operations seed tests only (tests/integration/test_simulation_*.py)
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
volume, applies the migrations (before starting the app, which then runs as
`classora_app`, as in `make dev`; the run checks it), the reference seeds and its own test fixture
data (`demo.*` accounts, `--seed 1`; see
[`_docs/testing-guidelines.md`](testing-guidelines.md)), runs the tests and always removes the stack again (`down -v`).
It needs Docker, uv and Node 24 (its fixtures and Playwright run on the host; the
first run downloads Chromium) but no `.env` (it uses `.env.example`), and it
can run while `make dev` is up without touching that stack or its data. A
failed run leaves Playwright traces, screenshots and the Compose logs
(`compose.log`) in `e2e/test-results/` and an HTML report in
`e2e/playwright-report/`. Open a trace from the repository root with
`corepack pnpm --filter @classora/e2e exec playwright show-trace test-results/<test>/trace.zip`
(the command runs in `e2e/`, so the path is relative to `e2e/`).
To keep the stack up for debugging, run `E2E_KEEP=1 make e2e`; the command it
prints removes it afterwards.

Each native API run prints a terminal coverage report for the API (`app/`), with
branch coverage and missing lines; coverage is native only. `make test` and
`make test-native` do not start the Compose database, and they can run while
`make dev` is up. Neither runs the Storybook or E2E tests.

Rules for writing tests (layers, fixtures and test data, authorization and
financial invariant tests, E2E and accessibility) are in
[`_docs/testing-guidelines.md`](testing-guidelines.md).

## Linting and type checks

```bash
make lint      # both apps and the E2E tests
make lint-api  # Ruff (lint and format check) and mypy (strict) on the API
make lint-web  # ESLint, Prettier check and TypeScript (strict) on the web app and the API client
make lint-e2e  # TypeScript (strict) and Prettier check on the E2E tests (e2e/)
```

Lint targets only check and never modify files. The web targets install
dependencies from the committed lockfile first, so no manual `pnpm install` is
needed. `make lint` and `make test-native` fail if any part fails; use `make -k` to
run every part regardless (`make test` always runs every part).

To build the web app for production: `make build-web` (output in
`apps/web/dist/`).

## API client

The web app calls the API through `@classora/api-client`
(`packages/api-client`), a typed client generated from the API's OpenAPI
document. After any API change, regenerate it and commit the result:

```bash
make api-client        # export openapi.json and the i18n code inventory (i18n-inventory.json), then regenerate the client types
make check-api-client  # regenerate and fail if packages/api-client differs from the commit (CI drift check)
```

Both need uv and Node 24, but not Docker, a database or `.env`. The web's
tests require a Spanish and English label for every code in
`i18n-inventory.json`. API conventions
(paths, Problem Details errors, pagination, versions) are in
[`_docs/api.md`](api.md).

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

```mermaid
flowchart TB
  local["💻 <b>Local development</b><br/>native stack or Docker Compose<br/>one git worktree per issue"]
  branch["🌿 Issue branch"]
  pr["🔀 Pull request<br/>to develop"]
  ci["⚙️ <b>GitHub Actions</b><br/>lint · unit · migrations<br/>billing invariants<br/>simulation · integration ×5<br/>API client drift<br/>web · Storybook · E2E<br/>grader path · security scans"]
  develop["develop"]
  main["main"]
  devenv["☁️ DEV on AWS"]
  prodenv["☁️ PROD on AWS"]

  local --> branch --> pr --> ci
  ci -->|"ci-required green<br/>and QA PASS"| develop
  ci -->|red| local
  develop -->|"release pull request<br/>(owner)"| main
  develop -->|"automatic deploy<br/>(deploy-dev workflow)"| devenv
  main -->|"deploy after owner approval<br/>(release-prod workflow)"| prodenv
```

Every push to `develop` runs the `deploy-dev` workflow
([`.github/workflows/deploy-dev.yml`](../.github/workflows/deploy-dev.yml)): it builds
and checks the arm64 images, pushes them to ECR, deploys them to DEV, checks
readiness and rolls back on failure. It is not part of `ci-required`, and it
skips with a notice while DEV is destroyed
([deploy runbook](runbooks/deploy.md)). Merging the `develop` → `main`
release pull request runs the `release-prod` workflow
([`.github/workflows/release-prod.yml`](../.github/workflows/release-prod.yml)): it
refuses a commit DEV never deployed, waits for the owner's approval in the
protected `production` environment, deploys the same image digests to PROD
and tags the release ([release runbook](runbooks/release.md)).

GitHub Actions ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs on
every pull request to `develop` or `main` and on pushes to both. Each check is
one `make` target, so a failing check can be reproduced locally with the same
command:

| CI job | Local command |
| --- | --- |
| `api-lint` | `make lint-api` |
| `api-unit` | `make test-unit` |
| `api-migrations` | `make test-migrations` |
| `api-billing-invariants` | `make test-billing-invariants` (default seeds on pull requests; pushes and manual runs add the run id as a seed) |
| `api-simulation` | `make test-simulation` |
| `api-integration (K/5)` | `make test-integration SHARD=K/5` (five parallel shards; the test files are dealt round-robin by path) |
| `web` | `make lint-web`, `make test-web`, `make build-web` |
| `web-storybook` | `make test-storybook` |
| `api-client` | `make check-api-client` |
| `e2e` | `make lint-e2e`, `make e2e`, `make check-pdf-image` (builds the API image and renders a sample PDF in es and en inside it) (on failure, uploads traces, the HTML report and the Compose logs as the `e2e-report` artifact) |
| `images` | `make build-images`, `make check-images`, `make check-pdf-image`, `make smoke-edge`, `make scan-image IMAGE=<ref>` for each image (CI builds the `api`, `web` (Caddy edge with the built SPA) and `pgbouncer` images natively for `linux/arm64` with `PLATFORM=linux/arm64`; locally the default is `linux/amd64`) |
| `infra` | `make check-observability` (validates the observability configuration with the pinned images: Collector, Prometheus, Loki, Tempo, Grafana provisioning; starts nothing), `make check-infra` (`terraform fmt -check` and `terraform validate` for every Terraform root, shellcheck on `infrastructure/scripts/` and `infrastructure/host/`, and actionlint on the workflows, in pinned containers; no AWS credentials) |
| `grader-path (ubuntu-24.04)`, `grader-path (ubuntu-24.04-arm)` | `make start`, `make seed-data-english`, a sign-in as `owner`, `make test` (amd64 and arm64), run in a "clean room" container that has only the Docker CLI (with Compose), GNU Make, bash and curl: no uv, Python, Node or Pango. Pull requests run `make test SHARDS=2 PYTEST_ARGS="tests/unit tests/integration/test_runtime_role.py tests/integration/test_health_ready.py tests/integration/test_receipts_pdf.py"` (with the full web tests); pushes and manual runs run the full `make test`. On failure, uploads `test-results/docker/` as the `grader-path-<runner>` artifact |
| `security-deps` | `make audit-deps` (pip-audit and `pnpm audit` over both lockfiles; also every Monday on `develop` by [`security-audit.yml`](../.github/workflows/security-audit.yml)) |
| `security-secrets` | `make scan-secrets` (gitleaks, full history); on pull requests also `make scan-secrets-diff BASE=<sha> HEAD=<sha>` |
| `security-sast` | `make sast` (Semgrep); Ruff's security (`S`) rules run in `make lint-api` |

Pull requests run only the jobs their changed files can affect (API jobs for
`apps/api/` and the PgBouncer image, web jobs for the web app, its packages and the Node toolchain
files; the `e2e` job for changes to either app, its packages, `e2e/`, the Compose
and Docker files, the Node toolchain files or `.env.example`; all of them for
`Makefile` and workflow changes; none for documentation). The `images` job runs
for changes to `infrastructure/docker/`, the API and Node lockfiles and manifests,
`.nvmrc`, `.dockerignore` and the SPA's security headers, and for `Makefile` and
workflow changes, not for application code alone. The `infra` job runs for
changes to `infrastructure/observability/`, `infrastructure/terraform/`,
`infrastructure/scripts/`, `infrastructure/host/` and `compose.yaml`, and for
`Makefile` and workflow changes.
The `api-client` drift check runs whenever the API or web jobs run. The three
security jobs and `grader-path` run on every pull request and push, documentation included.
`ci-required` is the one required check: it reports on every pull request and
fails if any job that ran failed or was cancelled, if a job was skipped although
its paths changed, or if a security job or `grader-path` was skipped.

### Security scans

```bash
make security                # audit-deps, scan-secrets and sast, as in CI (needs Docker, uv, Node 24)
make scan-image IMAGE=<ref>  # Trivy scan of a built image; fails on fixable CRITICAL findings
```

The dependency audit also runs weekly on `develop` (`security-audit.yml`), so a
new advisory against unchanged lockfiles shows up between pull requests.
Scanner versions are pinned in the `Makefile`. When a scan fails, fix the
cause (upgrade the dependency, remove the secret, change the code). If that is
not possible, add an exception with a reason and a review date as described in
[ADR-0016](adr/0016-ci-security-tooling.md); it is reviewed in the pull
request.

## How the work is delivered

Every change goes through one GitHub issue and a team of AI agents run from
one main session, with the repository owner taking the product decisions
([delivery process](process.md)):

```mermaid
flowchart TB
  owner["👤 <b>Owner</b><br/>asks for features and fixes<br/>answers decisions"]
  orch["🤖 <b>Orchestrator</b><br/>main session<br/>one issue in flight"]
  pm["🗂️ <b>PM agent</b><br/>investigates the code<br/>writes acceptance criteria<br/>and escalations"]
  eng["🛠️ <b>Engineer agent</b><br/>own git worktree<br/>code, tests, docs<br/>README gate"]
  pr["🔀 <b>Pull request</b><br/>full CI suites"]
  qa["🔍 <b>QA agent</b><br/>checks every criterion<br/>in its own clone and database<br/>cites the green CI run"]
  merged["✅ <b>Merged to develop</b><br/>issue closed"]

  owner -->|GitHub issue| orch
  orch --> pm
  pm -->|"decisions with a<br/>recommended option"| owner
  pm -->|groomed issue| eng
  eng --> pr --> qa
  qa -->|"FAIL: defects"| eng
  qa -->|"PASS + README verdict"| merged
```

## Deployment

Milestone M9 builds this AWS setup
([ADR-0010 to ADR-0014](architecture.md#9-deployment-operations-and-observability-approved)).
DEV is deployed: its host runs Caddy with a Let's Encrypt certificate and the
data and app stacks under systemd, receives every `develop` merge
automatically ([deploy runbook](runbooks/deploy.md)), and is operated
over SSM Session Manager ([host runbook](runbooks/host.md)). Its
database is dumped to S3 every night and its data volume snapshotted daily,
with tested restore drills ([restore runbook](runbooks/restore.md)).
DEV is monitored: its collector agent ships metrics, logs and traces to a
separate monitoring host (Grafana behind Caddy HTTPS with a login), whose
alerts, missed or failed backups included, e-mail the owner through Amazon SES
([monitoring runbook](runbooks/monitoring.md)). PROD runs the same host
design, with its own data, backups and telemetry, and receives only approved
releases ([release runbook](runbooks/release.md)). At the end of the
environments' window, PROD's database is archived as a verified final dump
before it is destroyed ([teardown runbook](runbooks/teardown.md)).
The hosts' t4g.small size was measured under a realistic center's load and
3× growth with `make load-test` on DEV, and the sizing and recovery objectives
are recorded ([load-test runbook](runbooks/load-test.md),
[ADR-0018](adr/0018-sizing-load-and-recovery-objectives.md)).
DEV, PROD and the monitoring host are created and destroyed with Terraform:

```mermaid
flowchart TB
  users["🌐 Browsers"]
  gha["⚙️ GitHub Actions<br/>OIDC, no stored keys"]
  owner["👤 Owner<br/>email inbox"]

  subgraph aws["AWS · us-east-2"]
    dns["Route 53<br/>app · dev · grafana"]
    ecr["ECR<br/>arm64 images by digest"]
    ssm["Parameter Store<br/>generated secrets"]
    deploy["SSM deploy command<br/>commit SHA only"]

    subgraph app["EC2 · DEV and PROD"]
      caddy["Caddy<br/>HTTPS · React SPA"]
      api["api<br/>FastAPI"]
      worker["worker<br/>background jobs"]
      pgb["PgBouncer"]
      pg[("PostgreSQL<br/>encrypted EBS volume")]
      agent["OTel Collector<br/>agent"]
    end

    subgraph mon["EC2 · monitoring (or on DEV)"]
      col["OTel Collector<br/>gateway"]
      prom["Prometheus<br/>metrics"]
      loki["Loki<br/>logs"]
      tempo["Tempo<br/>traces"]
      disk[("encrypted EBS volume<br/>telemetry, 7 to 15 days")]
      grafana["Grafana<br/>dashboards and alerts"]
    end

    backups[("S3 backups<br/>nightly dump, 14 days")]
    snaps[("EBS snapshots<br/>daily, 7 kept")]
    archive[("S3 archive<br/>final PROD dump, 365 days<br/>kept after teardown")]
    ses["SES<br/>alert email"]
    budget["AWS Budget<br/>US$75 a month<br/>Classora costs only"]
  end

  users --> dns --> caddy
  caddy --> api
  api --> pgb --> pg
  worker --> pg
  gha -->|push images| ecr
  gha -->|deploy| deploy --> app
  ecr --> app
  ssm --> app
  pg -->|nightly| backups
  pg -->|daily| snaps
  pg -.->|"before PROD<br/>is destroyed"| archive
  api -.-> agent
  worker -.-> agent
  agent --> col
  col --> prom
  col --> loki
  col --> tempo
  prom --> disk
  loki --> disk
  tempo --> disk
  grafana --> prom
  grafana --> loki
  grafana --> tempo
  grafana --> ses
  ses --> owner
  budget --> owner
```

DEV deploys on every merge to `develop`: the `deploy-dev` workflow sends the
commit SHA to the host through the SSM deploy command. PROD deploys from a
`main` release after the owner approves it, with the same command. Logs, metrics and traces stay on
the monitoring host's own disk, which can also run on DEV; the same stack
already runs locally as an optional profile
([Observability (optional)](#observability-optional)). Alerts and the budget alarm reach the
owner by email.

## Documentation

- [Product specification](spec.md)
- [Architecture](architecture.md)
- [Design system](design-system.md) (tokens, theming, accessibility rules)
- [API conventions](api.md) (paths, errors, pagination, versions, client workflow)
- [Security headers](security-headers.md) (Content Security Policy, API headers, `Origin` and CSRF)
- [Testing guidelines](testing-guidelines.md) (test layers, fixtures, permission matrix, financial invariants, E2E, accessibility)
- [Agent instructions](../AGENTS.md)
- [Delivery process](process.md) (agent workflow and contribution guidance)
- [Runbooks](runbooks/index.md) (AWS environments: Terraform, hosts, deploys, releases, backups, monitoring, secrets, teardown)
