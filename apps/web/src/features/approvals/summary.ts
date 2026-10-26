import { ApiError } from '@/app/api';
import type { TFunction } from 'i18next';
import type { RoleCode } from '@/features/auth/session';
import { isConflictCode } from '@/features/scheduling/create/rules';
import {
  describeChange,
  type AffectedSessionLike,
  type ChangeLike,
} from '@/features/tutors/changeRequests';
import { roleNameKey } from '@/features/users/roles';
import {
  APPROVAL_TYPE_LABELS,
  type ApprovalAction,
  type ApprovalRequestItem,
} from './api';

// Per-type display text of an approval request (#40 F3). A type without a
// renderer shows its code, so a newly registered type is never hidden.

interface RegistrationSubject {
  username: string;
  display_name: string;
  requested_role: RoleCode;
}

interface QualificationSubject {
  tutor_display_name: string;
  subject_name: string;
  education_level_name: string;
}

interface OccurrenceConflictSubject {
  class_session_id: string;
  series_id: string | null;
  starts_at: string;
  subject_name: string | null;
  student_display_names: string[];
  tutor_display_name: string | null;
  conflict_codes: string[];
}

// The data-validity codes a generated occurrence can also carry (#53 D3).
const REFERENCE_CODES = [
  'student_unknown',
  'student_inactive',
  'subject_unknown',
  'subject_inactive',
  'tutor_unknown',
  'tutor_inactive',
  'classroom_unknown',
  'classroom_inactive',
  'crosses_local_date',
] as const;

type ReferenceCode = (typeof REFERENCE_CODES)[number];

function isReferenceCode(code: string): code is ReferenceCode {
  return (REFERENCE_CODES as readonly string[]).includes(code);
}

function isOccurrenceConflict(
  subject: unknown,
): subject is OccurrenceConflictSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.starts_at === 'string' &&
    Array.isArray(value.student_display_names) &&
    Array.isArray(value.conflict_codes)
  );
}

/** The series-occurrence conflict subject of `item` (#53), or null. */
export function occurrenceConflictSubject(
  item: ApprovalRequestItem,
): OccurrenceConflictSubject | null {
  return item.request_type === 'scheduling.occurrence_conflict' &&
    isOccurrenceConflict(item.subject)
    ? item.subject
    : null;
}

/** A short translated name of a conflict or data-validity code; an unknown
 * code is shown as it is. */
export function occurrenceCodeName(code: string, t: TFunction): string {
  if (isConflictCode(code)) {
    return t(`scheduling.rules.${code}`);
  }
  if (isReferenceCode(code)) {
    return t(`approvals.occurrenceCodes.${code}`);
  }
  return code;
}

function isQualification(subject: unknown): subject is QualificationSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.tutor_display_name === 'string' &&
    typeof value.subject_name === 'string' &&
    typeof value.education_level_name === 'string'
  );
}

/** The tutor qualification subject of `item` (#44), or null. */
export function qualificationSubject(
  item: ApprovalRequestItem,
): QualificationSubject | null {
  return item.request_type === 'staff.qualification' &&
    isQualification(item.subject)
    ? item.subject
    : null;
}

function isRegistration(subject: unknown): subject is RegistrationSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.username === 'string' &&
    typeof value.display_name === 'string' &&
    typeof value.requested_role === 'string'
  );
}

interface ReassignmentSubject {
  /** Null only for a series offer that covered no session (#233 E2). */
  class_session_id: string | null;
  subject_name: string;
  starts_at: string | null;
  tutor_name: string;
  outcome: 'declined' | 'expired';
  decline_note: string | null;
  session_state: string | null;
  /** Set for a series offer (#233 E2). */
  class_series_id?: string | null;
  from_date?: string | null;
  occurrence_count?: number | null;
  /** #55 D2: `substitution` for a substitute's declined or expired offer. */
  kind?: 'assignment' | 'substitution';
  /** The tutor still teaching a substitution's session (#55). */
  original_tutor_name?: string | null;
}

function isReassignment(subject: unknown): subject is ReassignmentSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  const series = typeof value.class_series_id === 'string';
  return (
    (typeof value.class_session_id === 'string' ||
      (series && value.class_session_id === null)) &&
    typeof value.subject_name === 'string' &&
    (typeof value.starts_at === 'string' ||
      (series && value.starts_at === null)) &&
    typeof value.tutor_name === 'string' &&
    (value.outcome === 'declined' || value.outcome === 'expired')
  );
}

/** The offer reassignment subject of `item` (#52 H3), or null. */
export function reassignmentSubject(
  item: ApprovalRequestItem,
): ReassignmentSubject | null {
  return item.request_type === 'scheduling.offer_reassignment' &&
    isReassignment(item.subject)
    ? item.subject
    : null;
}

/** The session page a reassignment item links to (#52 H3), or null. */
export function reassignmentLink(item: ApprovalRequestItem): string | null {
  const subject = reassignmentSubject(item);
  return subject?.class_session_id
    ? `/schedule/sessions/${encodeURIComponent(subject.class_session_id)}`
    : null;
}

interface SessionRequestSubject {
  student_name: string;
  subject_name: string;
  education_level_name: string;
  starts_at: string;
  ends_at: string;
  duration_minutes: number;
  requested_by: string;
  note: string | null;
}

function isSessionRequest(subject: unknown): subject is SessionRequestSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.student_name === 'string' &&
    typeof value.subject_name === 'string' &&
    typeof value.education_level_name === 'string' &&
    typeof value.starts_at === 'string' &&
    typeof value.duration_minutes === 'number' &&
    typeof value.requested_by === 'string'
  );
}

/** The family session request subject of `item` (#59 E1), or null. */
export function sessionRequestSubject(
  item: ApprovalRequestItem,
): SessionRequestSubject | null {
  return item.request_type === 'scheduling.session_request' &&
    isSessionRequest(item.subject)
    ? item.subject
    : null;
}

/**
 * The staff review page of a pending session request (#60 H2): the inbox
 * shows a "Review" link instead of Approve / Reject, which can't carry the
 * tutor (the API lists no allowed actions for these items). Null for another
 * type, a decided item or an older summary without the request id.
 */
export function sessionRequestReviewLink(
  item: ApprovalRequestItem,
): string | null {
  if (
    !sessionRequestSubject(item) ||
    item.status !== 'pending' ||
    typeof item.subject.session_request_id !== 'string'
  ) {
    return null;
  }
  return reviewPath(item.subject.session_request_id);
}

interface ChangeRequestSubject {
  change_request_id: string;
  kind: 'cancellation' | 'reschedule';
  initiated_by: string;
  student_name: string;
  subject_name: string;
  starts_at: string;
  proposed_starts_at: string | null;
  classification: 'on_time' | 'late' | 'no_show' | 'excused';
  status: string;
}

function isChangeRequest(subject: unknown): subject is ChangeRequestSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.change_request_id === 'string' &&
    (value.kind === 'cancellation' || value.kind === 'reschedule') &&
    typeof value.student_name === 'string' &&
    typeof value.subject_name === 'string' &&
    typeof value.starts_at === 'string' &&
    (value.classification === 'on_time' ||
      value.classification === 'late' ||
      value.classification === 'no_show' ||
      value.classification === 'excused')
  );
}

/** The cancellation or reschedule request subject of `item` (#62 C6), or
 * null. */
export function changeRequestSubject(
  item: ApprovalRequestItem,
): ChangeRequestSubject | null {
  return item.request_type === 'scheduling.change_request' &&
    isChangeRequest(item.subject)
    ? item.subject
    : null;
}

/** The review page of a change request (#62 G1). */
export function changeRequestReviewPath(requestId: string): string {
  return `/schedule/change-requests/${encodeURIComponent(requestId)}`;
}

/**
 * The staff review page of a pending change request (#62 G2): the inbox
 * shows a "Review" link instead of Approve / Reject (the API lists no
 * allowed actions for these items). Null otherwise.
 */
export function changeRequestReviewLink(
  item: ApprovalRequestItem,
): string | null {
  const subject = changeRequestSubject(item);
  if (!subject || item.status !== 'pending') {
    return null;
  }
  return changeRequestReviewPath(subject.change_request_id);
}

/** The review page of a pending item decided on its own page (#60, #62),
 * or null. */
export function itemReviewLink(item: ApprovalRequestItem): string | null {
  return sessionRequestReviewLink(item) ?? changeRequestReviewLink(item);
}

const REVIEW_ROUTE_CODES: Readonly<Record<string, string>> = {
  session_request_review_route_required: '/schedule/requests/',
  change_request_review_route_required: '/schedule/change-requests/',
};

/** The review page named by `409 session_request_review_route_required`
 * (#60) or `change_request_review_route_required` (#62). */
export function reviewLinkOf(error: unknown): string | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  const prefix = REVIEW_ROUTE_CODES[error.code];
  if (prefix === undefined) {
    return null;
  }
  const details: unknown = error.problem?.details;
  const path =
    typeof details === 'object' && details !== null
      ? (details as Record<string, unknown>).review_path
      : undefined;
  if (typeof path === 'string' && path.startsWith(prefix)) {
    return path;
  }
  return null;
}

/** The review page of a session request (#60 H1). */
export function reviewPath(requestId: string): string {
  return `/schedule/requests/${encodeURIComponent(requestId)}`;
}

interface AvailabilityChangeSubject extends ChangeLike {
  tutor: { id: string; display_name: string };
  affected_count: number;
  affected_sessions: (AffectedSessionLike & { state: string })[];
  change_request_status: string;
}

function isAvailabilityChange(
  subject: unknown,
): subject is AvailabilityChangeSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  const tutor = value.tutor as Record<string, unknown> | null | undefined;
  return (
    (value.kind === 'weekly' ||
      value.kind === 'exception_create' ||
      value.kind === 'exception_delete') &&
    typeof value.change === 'object' &&
    value.change !== null &&
    typeof tutor === 'object' &&
    tutor !== null &&
    typeof tutor.id === 'string' &&
    typeof tutor.display_name === 'string' &&
    typeof value.affected_count === 'number' &&
    Array.isArray(value.affected_sessions)
  );
}

/** The availability change subject of `item` (#63 H2), or null. */
export function availabilityChangeSubject(
  item: ApprovalRequestItem,
): AvailabilityChangeSubject | null {
  return item.request_type === 'staff.availability_change' &&
    isAvailabilityChange(item.subject)
    ? item.subject
    : null;
}

/** The tutor's availability section an availability change links to (#63
 * H2), or null. */
export function availabilityChangeLink(
  item: ApprovalRequestItem,
): string | null {
  const subject = availabilityChangeSubject(item);
  return subject
    ? `/tutors/${encodeURIComponent(subject.tutor.id)}#availability`
    : null;
}

interface SessionCloseoutSubject {
  class_session_id: string;
  starts_at: string;
  subject_name: string;
  tutor_display_name: string;
  kind: string;
  scheduled_minutes: number;
  actual_minutes: number | null;
  participant_count: number;
  /** #78 C4 (absent before #78's API). */
  kind_of_review?: 'first' | 'resubmission' | 'correction';
  approved_minutes?: number | null;
  /** #301: set when a reviewer submitted it on the tutor's behalf. */
  on_behalf_by_display_name?: string | null;
}

function isSessionCloseout(
  subject: unknown,
): subject is SessionCloseoutSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.class_session_id === 'string' &&
    typeof value.starts_at === 'string' &&
    typeof value.subject_name === 'string' &&
    typeof value.tutor_display_name === 'string' &&
    typeof value.scheduled_minutes === 'number' &&
    typeof value.participant_count === 'number'
  );
}

/** The session closeout subject of `item` (#77 G4), or null. */
export function sessionCloseoutSubject(
  item: ApprovalRequestItem,
): SessionCloseoutSubject | null {
  return item.request_type === 'academics.session_closeout' &&
    isSessionCloseout(item.subject)
    ? item.subject
    : null;
}

/** The staff session page's Closeout section a closeout item links to
 * (#77 G4; #78 G2: the "Review" link), or null. */
export function sessionCloseoutLink(item: ApprovalRequestItem): string | null {
  const subject = sessionCloseoutSubject(item);
  return subject
    ? `/schedule/sessions/${encodeURIComponent(subject.class_session_id)}#closeout`
    : null;
}

/** #78 G2: what a closeout item asks ("New", "Resubmitted", "Correction"),
 * " · On behalf" when a reviewer submitted it for the tutor (#301b E4) and
 * the minutes approved now (a correction), as text; empty without them. */
export function sessionCloseoutReviewLine(
  item: ApprovalRequestItem,
  t: TFunction,
): string {
  const subject = sessionCloseoutSubject(item);
  if (!subject?.kind_of_review) {
    return '';
  }
  const parts = [t(`approvals.closeout.kinds.${subject.kind_of_review}`)];
  if (subject.on_behalf_by_display_name) {
    parts.push(t('approvals.closeout.onBehalf'));
  }
  if (typeof subject.approved_minutes === 'number') {
    parts.push(
      t('approvals.closeout.approvedMinutes', {
        minutes: subject.approved_minutes,
      }),
    );
  }
  return parts.join(' · ');
}

interface ProgressReportSubject {
  /** #83 AM1 (absent before it). */
  report_id?: string;
  student: { id: string; display_name: string };
  author: { display_name: string };
  kind: 'weekly' | 'monthly' | 'on_demand';
  period_start: string;
  period_end: string;
  subject: { id: string; name: string } | null;
  submitted_at: string | null;
}

function isProgressReport(subject: unknown): subject is ProgressReportSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  const student = value.student as Record<string, unknown> | null | undefined;
  const author = value.author as Record<string, unknown> | null | undefined;
  return (
    typeof student === 'object' &&
    student !== null &&
    typeof student.display_name === 'string' &&
    typeof author === 'object' &&
    author !== null &&
    typeof author.display_name === 'string' &&
    typeof value.period_start === 'string' &&
    typeof value.period_end === 'string' &&
    (value.subject === null ||
      (typeof value.subject === 'object' &&
        typeof (value.subject as Record<string, unknown>).name === 'string'))
  );
}

/** The progress report subject of `item` (#83 H2), or null. Decided on the
 * report's review page (#83b adds the "Review" link): the inbox lists no
 * decisions for it. */
export function progressReportSubject(
  item: ApprovalRequestItem,
): ProgressReportSubject | null {
  return item.request_type === 'academics.progress_report' &&
    isProgressReport(item.subject)
    ? item.subject
    : null;
}

/** Formats an instant for a summary (the center zone, from useFormatters). */
export type InstantFormatter = (instant: string) => string;

/** Formats a local date (`YYYY-MM-DD`) for a summary (from useFormatters). */
export type DateFormatter = (localDate: string) => string;

const ACTION_LABELS = {
  approve: 'approvals.actions.approve',
  reject: 'approvals.actions.reject',
  request_changes: 'approvals.actions.requestChanges',
} as const;

const NAMED_ACTION_LABELS = {
  approve: 'approvals.actions.approveFor',
  reject: 'approvals.actions.rejectFor',
  request_changes: 'approvals.actions.requestChangesFor',
} as const;

/**
 * The label of a decision button: a reassignment item (#52 H3) is "Mark
 * resolved" / "Dismiss"; every other type "Approve" / "Reject".
 */
export function actionLabelKey(type: string, action: ApprovalAction) {
  if (type === 'scheduling.offer_reassignment') {
    if (action === 'approve') {
      return 'approvals.actions.markResolved' as const;
    }
    if (action === 'reject') {
      return 'approvals.actions.dismiss' as const;
    }
  }
  return ACTION_LABELS[action];
}

/** The accessible name of a decision button for one row (`name`). */
export function namedActionLabelKey(type: string, action: ApprovalAction) {
  if (type === 'scheduling.offer_reassignment') {
    if (action === 'approve') {
      return 'approvals.actions.markResolvedFor' as const;
    }
    if (action === 'reject') {
      return 'approvals.actions.dismissFor' as const;
    }
  }
  return NAMED_ACTION_LABELS[action];
}

/** The type's label, or its code when the page doesn't know the type. */
export function typeLabel(type: string, t: TFunction): string {
  const key = APPROVAL_TYPE_LABELS[type];
  return key ? t(key) : type;
}

/** The registration subject of `item`, or null for another type. */
export function registrationSubject(
  item: ApprovalRequestItem,
): RegistrationSubject | null {
  return item.request_type === 'identity.registration' &&
    isRegistration(item.subject)
    ? item.subject
    : null;
}

/**
 * One line naming what the request is about: for a registration "display
 * name (username) · requested role"; for a tutor qualification "tutor —
 * subject (level)"; for an offer reassignment (#52) "Declined by tutor ·
 * subject · date time" (or "Expired …"), and for a series offer (#233 G3)
 * "Declined by tutor · subject · weekly series from date (n sessions)";
 * for a substitute's offer (#55 H3) "Substitute tutor declined · subject ·
 * date time · still taught by original";
 * for a series occurrence in conflict
 * (#53) "Series occurrence <date, time> — <students> · <subject>: <codes>";
 * for a family's session request (#59) "<student> · <subject> (<level>) ·
 * <date, time> · <minutes> min · requested by <name>";
 * for a change request (#62) "<kind> · <student> · <subject> · <date, time>
 * [→ <proposed>] · <classification>";
 * for an availability change (#63) "<tutor> · <change> · <n> classes
 * affected";
 * for a session closeout (#77) "<subject> · <date, time> · <tutor> ·
 * <actual> of <scheduled> min · <n> students";
 * for a progress report (#83) "<student> · <start> – <end> · <subject> ·
 * <author>" (no subject part for a whole-student report);
 * for an unknown type the type code.
 */
export function approvalSummary(
  item: ApprovalRequestItem,
  t: TFunction,
  formatInstant: InstantFormatter = (instant) => instant,
  formatDate: DateFormatter = (localDate) => localDate,
) {
  const registration = registrationSubject(item);
  if (registration) {
    return t('approvals.summary.registration', {
      name: registration.display_name,
      username: registration.username,
      role: t(roleNameKey(registration.requested_role)),
    });
  }
  const qualification = qualificationSubject(item);
  if (qualification) {
    return t('approvals.summary.qualification', {
      tutor: qualification.tutor_display_name,
      subject: qualification.subject_name,
      level: qualification.education_level_name,
    });
  }
  const reassignment = reassignmentSubject(item);
  if (reassignment?.class_series_id && reassignment.from_date) {
    return t(
      reassignment.outcome === 'declined'
        ? 'approvals.summary.seriesReassignmentDeclined'
        : 'approvals.summary.seriesReassignmentExpired',
      {
        tutor: reassignment.tutor_name,
        subject: reassignment.subject_name,
        date: formatDate(reassignment.from_date),
        count: reassignment.occurrence_count ?? 0,
      },
    );
  }
  if (
    reassignment?.starts_at &&
    reassignment.kind === 'substitution' &&
    reassignment.original_tutor_name
  ) {
    return t(
      reassignment.outcome === 'declined'
        ? 'approvals.summary.substitutionDeclined'
        : 'approvals.summary.substitutionExpired',
      {
        tutor: reassignment.tutor_name,
        subject: reassignment.subject_name,
        when: formatInstant(reassignment.starts_at),
        original: reassignment.original_tutor_name,
      },
    );
  }
  if (reassignment?.starts_at) {
    return t(
      reassignment.outcome === 'declined'
        ? 'approvals.summary.reassignmentDeclined'
        : 'approvals.summary.reassignmentExpired',
      {
        tutor: reassignment.tutor_name,
        subject: reassignment.subject_name,
        when: formatInstant(reassignment.starts_at),
      },
    );
  }
  const sessionRequest = sessionRequestSubject(item);
  if (sessionRequest) {
    return t('approvals.summary.sessionRequest', {
      student: sessionRequest.student_name,
      subject: sessionRequest.subject_name,
      level: sessionRequest.education_level_name,
      when: formatInstant(sessionRequest.starts_at),
      minutes: sessionRequest.duration_minutes,
      requester: sessionRequest.requested_by,
    });
  }
  const change = changeRequestSubject(item);
  if (change) {
    const shared = {
      kind: t(`scheduling.changeRequest.kinds.${change.kind}`),
      student: change.student_name,
      subject: change.subject_name,
      when: formatInstant(change.starts_at),
      classification: t(
        `scheduling.changeRequest.classification.${change.classification}`,
      ),
    };
    return change.proposed_starts_at
      ? t('approvals.summary.changeRequestReschedule', {
          ...shared,
          proposed: formatInstant(change.proposed_starts_at),
        })
      : t('approvals.summary.changeRequest', shared);
  }
  const availability = availabilityChangeSubject(item);
  if (availability) {
    return t('approvals.summary.availabilityChange', {
      tutor: availability.tutor.display_name,
      change: describeChange(availability, t, formatDate),
      count: availability.affected_count,
    });
  }
  const closeout = sessionCloseoutSubject(item);
  if (closeout) {
    return t('approvals.summary.sessionCloseout', {
      subject: closeout.subject_name,
      when: formatInstant(closeout.starts_at),
      tutor: closeout.tutor_display_name,
      actual: closeout.actual_minutes ?? 0,
      scheduled: closeout.scheduled_minutes,
      count: closeout.participant_count,
    });
  }
  const report = progressReportSubject(item);
  if (report) {
    const shared = {
      student: report.student.display_name,
      start: formatDate(report.period_start),
      end: formatDate(report.period_end),
      author: report.author.display_name,
    };
    return report.subject
      ? t('approvals.summary.progressReport', {
          ...shared,
          subject: report.subject.name,
        })
      : t('approvals.summary.progressReportNoSubject', shared);
  }
  const occurrence = occurrenceConflictSubject(item);
  if (occurrence) {
    return t('approvals.summary.occurrenceConflict', {
      when: formatInstant(occurrence.starts_at),
      students: occurrence.student_display_names.join(', '),
      subject: occurrence.subject_name ?? '',
      codes: occurrence.conflict_codes
        .map((code) => occurrenceCodeName(code, t))
        .join(', '),
    });
  }
  return item.request_type;
}

/** What a row's buttons name: a registration's username, else the summary. */
export function itemName(
  item: ApprovalRequestItem,
  t: TFunction,
  formatInstant?: InstantFormatter,
  formatDate?: DateFormatter,
): string {
  return (
    registrationSubject(item)?.username ??
    approvalSummary(item, t, formatInstant, formatDate)
  );
}
