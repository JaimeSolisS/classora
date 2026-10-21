import type { TFunction } from 'i18next';
import type { RoleCode } from '@/features/auth/session';
import { isConflictCode } from '@/features/scheduling/create/rules';
import { roleNameKey } from '@/features/users/roles';
import { DEFAULT_LANGUAGE, type Language } from '@/i18n/language';
import { DEFAULT_CENTER_TIME_ZONE, formatInstant } from '@/lib/time';
import { APPROVAL_TYPE_LABELS, type ApprovalRequestItem } from './api';

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

/** Where and in which language a summary's times are shown (#53 G5). */
export interface SummaryContext {
  timeZone: string;
  language: Language;
}

const DEFAULT_CONTEXT: SummaryContext = {
  timeZone: DEFAULT_CENTER_TIME_ZONE,
  language: DEFAULT_LANGUAGE,
};

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
 * subject (level)"; for a series occurrence in conflict "Series occurrence
 * <date, time in the center zone> — <students> · <subject>: <codes>"; for an
 * unknown type the type code.
 */
export function approvalSummary(
  item: ApprovalRequestItem,
  t: TFunction,
  context: SummaryContext = DEFAULT_CONTEXT,
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
  const occurrence = occurrenceConflictSubject(item);
  if (occurrence) {
    return t('approvals.summary.occurrenceConflict', {
      when: formatInstant(occurrence.starts_at, {
        timeZone: context.timeZone,
        language: context.language,
      }),
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
  context: SummaryContext = DEFAULT_CONTEXT,
): string {
  return (
    registrationSubject(item)?.username ?? approvalSummary(item, t, context)
  );
}
