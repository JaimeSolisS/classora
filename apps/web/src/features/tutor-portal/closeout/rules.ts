import { ApiError } from '@/app/api';
import type { SessionAttendance } from '@/features/tutor-portal/attendance/api';
import type { SessionCloseoutView } from './api';

// The closeout flow's pure rules (#77 G1, G2), mirroring the API's
// (academics/closeout_rules.py): the minutes range, the summary fields and
// which are required (none when every participant is absent), the server's
// 422 field errors mapped back onto the fields, and when My schedule
// offers the closeout.

export const MINUTES_MIN = 0;
export const MINUTES_MAX = 720;
export const SUMMARY_MAX = 2000;

export const SUMMARY_FIELDS = [
  'topics',
  'performance',
  'homework',
  'next_steps',
] as const;

export type SummaryField = (typeof SUMMARY_FIELDS)[number];

const REQUIRED: readonly SummaryField[] = [
  'topics',
  'performance',
  'next_steps',
];

export type SummaryDraft = Readonly<Record<SummaryField, string>>;

export const EMPTY_SUMMARY: SummaryDraft = {
  topics: '',
  performance: '',
  homework: '',
  next_steps: '',
};

export type SummaryErrorCode = 'summary_required' | 'too_long';

export type SummaryErrors = Partial<Record<SummaryField, SummaryErrorCode>>;

export function isRequired(field: SummaryField, allAbsent: boolean): boolean {
  return !allAbsent && REQUIRED.includes(field);
}

/** The summary's field errors (trimmed, as the API checks them). */
export function summaryErrors(
  draft: SummaryDraft,
  allAbsent: boolean,
): SummaryErrors {
  const errors: SummaryErrors = {};
  for (const field of SUMMARY_FIELDS) {
    const value = draft[field].trim();
    if (value === '') {
      if (isRequired(field, allAbsent)) {
        errors[field] = 'summary_required';
      }
    } else if (value.length > SUMMARY_MAX) {
      errors[field] = 'too_long';
    }
  }
  return errors;
}

/** The minutes field's error, or null (a whole number from 0 to 720). */
export function minutesError(value: string): 'minutes_range' | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) {
    return 'minutes_range';
  }
  const minutes = Number(trimmed);
  return minutes < MINUTES_MIN || minutes > MINUTES_MAX
    ? 'minutes_range'
    : null;
}

/** The students without a mark, by display name (step 1's list). */
export function unmarkedNames(attendance: SessionAttendance): string[] {
  return attendance.items
    .filter((item) => item.status === null)
    .map((item) => item.student.display_name);
}

/** Every participant marked absent: the summary is optional. */
export function allAbsent(attendance: SessionAttendance): boolean {
  return (
    attendance.items.length > 0 &&
    attendance.items.every((item) => item.status === 'absent')
  );
}

/** The body's summary: trimmed, an empty field left out. */
export function summaryBody(
  draft: SummaryDraft,
): Partial<Record<SummaryField, string>> {
  const body: Partial<Record<SummaryField, string>> = {};
  for (const field of SUMMARY_FIELDS) {
    const value = draft[field].trim();
    if (value !== '') {
      body[field] = value;
    }
  }
  return body;
}

/** A server 422's errors on `summary.<field>`, mapped back onto the fields. */
export function serverSummaryErrors(error: unknown): SummaryErrors {
  const errors: SummaryErrors = {};
  if (!(error instanceof ApiError) || error.status !== 422) {
    return errors;
  }
  for (const item of error.problem?.errors ?? []) {
    const [, section, field] = item.path;
    if (
      section === 'summary' &&
      (SUMMARY_FIELDS as readonly unknown[]).includes(field)
    ) {
      errors[field as SummaryField] =
        item.code === 'too_long' ? 'too_long' : 'summary_required';
    }
  }
  return errors;
}

/**
 * G2: a tutor's entry links to the closeout once it can be submitted or
 * read; #305 C1: a completed one whose closeout came back with changes
 * requested (the dialog's lazy closeout read) links to "Fix closeout".
 */
export type CloseoutLink = 'submit' | 'view' | 'fix' | null;

export function tutorCloseoutLink(
  item: { state: string; starts_at: string },
  now: number,
  closeoutStatus?: string | null,
): CloseoutLink {
  if (item.state === 'completed') {
    return closeoutStatus === 'changes_requested' ? 'fix' : 'view';
  }
  if (item.state === 'confirmed' && Date.parse(item.starts_at) <= now) {
    return 'submit';
  }
  return null;
}

/** #305 A2: an edit mode's summary draft, prefilled (null → empty). */
export function summaryDraftOf(
  summary: Partial<Record<SummaryField, string | null>> | null,
): SummaryDraft {
  return {
    topics: summary?.topics ?? '',
    performance: summary?.performance ?? '',
    homework: summary?.homework ?? '',
    next_steps: summary?.next_steps ?? '',
  };
}

export const NOTE_MAX = 1000;

/**
 * #305 A5: the reviewer note's (resubmit, optional) or the correction
 * reason's (required) error code, trimmed 1-1000.
 */
export function noteError(
  note: string,
  required: boolean,
): 'reason_required' | 'reason_too_long' | null {
  const length = note.trim().length;
  if (length === 0) {
    return required ? 'reason_required' : null;
  }
  return length > NOTE_MAX ? 'reason_too_long' : null;
}

/** #305 A1: the edit modes of a recorded closeout. */
export type EditMode = 'resubmit' | 'correction';

/** Whether the GET's flags allow an edit mode. */
export function editAllowed(
  closeout: SessionCloseoutView,
  mode: EditMode | null | undefined,
): mode is EditMode {
  if (!closeout.closeout || !mode) {
    return false;
  }
  return mode === 'resubmit'
    ? closeout.can_resubmit
    : closeout.can_request_correction;
}
