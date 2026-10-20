import type { TFunction } from 'i18next';
import type { RoleCode } from '@/features/auth/session';
import { roleNameKey } from '@/features/users/roles';
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
 * subject (level)"; for an unknown type the type code.
 */
export function approvalSummary(item: ApprovalRequestItem, t: TFunction) {
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
  return item.request_type;
}

/** What a row's buttons name: a registration's username, else the summary. */
export function itemName(item: ApprovalRequestItem, t: TFunction): string {
  return registrationSubject(item)?.username ?? approvalSummary(item, t);
}
