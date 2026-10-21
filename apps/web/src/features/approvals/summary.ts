import type { TFunction } from 'i18next';
import type { RoleCode } from '@/features/auth/session';
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
  class_session_id: string;
  subject_name: string;
  starts_at: string;
  tutor_name: string;
  outcome: 'declined' | 'expired';
  decline_note: string | null;
  session_state: string;
}

function isReassignment(subject: unknown): subject is ReassignmentSubject {
  if (typeof subject !== 'object' || subject === null) {
    return false;
  }
  const value = subject as Record<string, unknown>;
  return (
    typeof value.class_session_id === 'string' &&
    typeof value.subject_name === 'string' &&
    typeof value.starts_at === 'string' &&
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
  return subject
    ? `/schedule/sessions/${encodeURIComponent(subject.class_session_id)}`
    : null;
}

/** Formats an instant for a summary (the center zone, from useFormatters). */
export type InstantFormatter = (instant: string) => string;

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
 * subject · date time" (or "Expired …"); for an unknown type the type code.
 */
export function approvalSummary(
  item: ApprovalRequestItem,
  t: TFunction,
  formatInstant: InstantFormatter = (instant) => instant,
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
  if (reassignment) {
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
  return item.request_type;
}

/** What a row's buttons name: a registration's username, else the summary. */
export function itemName(
  item: ApprovalRequestItem,
  t: TFunction,
  formatInstant?: InstantFormatter,
): string {
  return (
    registrationSubject(item)?.username ??
    approvalSummary(item, t, formatInstant)
  );
}
