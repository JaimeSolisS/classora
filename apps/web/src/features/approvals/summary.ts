import type { TFunction } from 'i18next';
import type { RoleCode } from '@/features/auth/session';
import { isConflictCode } from '@/features/scheduling/create/rules';
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
