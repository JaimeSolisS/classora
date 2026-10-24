import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import {
  DURATIONS,
  type RequestDuration,
} from '@/features/family-portal/requests/api';
import { retryMinutes } from '@/features/family-portal/requests/rules';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import { addDays, localDateTimeToInstant, type LocalDate } from '@/lib/time';
import type {
  ChangeRequestCreate,
  ChangeRequestItem,
  ChangeRequestKind,
  StatusFilter,
} from './api';

// The rules of the change request pages (#62b), without React.

// ------------------------------------------------- A1: who sees the button

/** Whose schedule the details dialog belongs to (#67 `?for=`). */
export type WhoseKind = 'teaching' | 'student';

/**
 * Whether "Request a change" is offered for an item (A1): a confirmed
 * individual session that hasn't started, which is the caller's to request
 * (a family item in a family schedule, or a tutor item in the tutor's own
 * "My teaching"). A UX filter only: the server decides.
 */
export function canRequestChange(
  entry: ScheduleEntry,
  whose: WhoseKind,
  now: number,
): boolean {
  const { item } = entry;
  const own =
    (entry.audience === 'family' && whose === 'student') ||
    (entry.audience === 'tutor' && whose === 'teaching');
  return (
    own &&
    item.state === 'confirmed' &&
    item.kind === 'individual' &&
    Date.parse(item.starts_at) > now
  );
}

/** Who makes the request (the server's `initiated_by`). */
export function initiatorOf(entry: ScheduleEntry): 'family' | 'tutor' {
  return entry.audience === 'tutor' ? 'tutor' : 'family';
}

/** The session's length in whole minutes. */
export function durationMinutes(item: {
  starts_at: string;
  ends_at: string;
}): number {
  return Math.round(
    (Date.parse(item.ends_at) - Date.parse(item.starts_at)) / 60_000,
  );
}

/** A family can move a class only to a slot of one of #59's lengths. */
export function slotDuration(minutes: number): RequestDuration | null {
  return (DURATIONS as readonly number[]).includes(minutes)
    ? (minutes as RequestDuration)
    : null;
}

// ---------------------------------------------- A2: the family's slot pages

/** The dates one page of slots covers (the API's limit). */
export const SLOT_PAGE_DAYS = 14;

/** How far ahead a family may search (#59's `range_too_far`). */
export const SLOT_MAX_DAYS_AHEAD = 60;

/** The dates `[from, to]` of the page starting on `start`, never after
 * today + 60 days. */
export function slotPage(
  start: LocalDate,
  today: LocalDate,
): { from: LocalDate; to: LocalDate } {
  const last = addDays(start, SLOT_PAGE_DAYS - 1);
  const limit = addDays(today, SLOT_MAX_DAYS_AHEAD);
  return { from: start, to: last > limit ? limit : last };
}

export function canPageBack(start: LocalDate, today: LocalDate): boolean {
  return start > today;
}

export function canPageForward(start: LocalDate, today: LocalDate): boolean {
  return addDays(start, SLOT_PAGE_DAYS) <= addDays(today, SLOT_MAX_DAYS_AHEAD);
}

export function previousPage(start: LocalDate, today: LocalDate): LocalDate {
  const previous = addDays(start, -SLOT_PAGE_DAYS);
  return previous < today ? today : previous;
}

// ------------------------------------------------- A2: the tutor's proposal

export type ProposalProblem = 'required' | 'invalid' | 'past';

const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;
const LOCAL_TIME = /^\d{2}:\d{2}$/;

/**
 * A tutor's proposed start from a local date and time in the center zone
 * (`lib/time`, DST-aware). The client checks only that it is complete, on
 * a whole minute and after now; every other rule is the server's (#62 B2).
 */
export function tutorProposal(
  date: string,
  time: string,
  timeZone: string,
  now: number,
): { startsAt: string } | { problem: ProposalProblem } {
  if (!date || !time) {
    return { problem: 'required' };
  }
  if (!LOCAL_DATE.test(date) || !LOCAL_TIME.test(time)) {
    return { problem: 'invalid' };
  }
  let startsAt: string;
  try {
    startsAt = localDateTimeToInstant(date, time, timeZone);
  } catch {
    return { problem: 'invalid' };
  }
  if (Date.parse(startsAt) <= now) {
    return { problem: 'past' };
  }
  return { startsAt };
}

/** The end of a proposal: the start plus the session's length. */
export function proposalEnd(startsAt: string, minutes: number): string {
  return new Date(Date.parse(startsAt) + minutes * 60_000).toISOString();
}

// -------------------------------------------------------- A2: the request

/** The note's maximum length once trimmed (#62 B1). */
export const NOTE_MAX_LENGTH = 500;

export interface NewChangeRequest {
  sessionId: string;
  kind: ChangeRequestKind;
  /** A reschedule's proposed start (an instant). */
  proposedStartsAt?: string;
  note: string;
}

/** The POST body: the proposal only for a reschedule; the note trimmed and
 * left out when empty. */
export function createBody(request: NewChangeRequest): ChangeRequestCreate {
  const note = request.note.trim();
  return {
    class_session_id: request.sessionId,
    kind: request.kind,
    ...(request.kind === 'reschedule' && request.proposedStartsAt
      ? { proposed_starts_at: request.proposedStartsAt }
      : {}),
    ...(note ? { note } : {}),
  };
}

// ---------------------------------------------------- A4: a refused send

export type RefusalCode =
  | 'slot_unavailable'
  | 'change_request_pending'
  | 'change_request_session_started'
  | 'class_session_not_changeable'
  | 'change_request_kind_unsupported'
  | 'student_consent_required'
  | 'rate_limited'
  | 'proposal_in_past'
  | 'proposal_unchanged'
  | 'not_found';

/** The bounds codes of a proposed time (#48 `validate_session_bounds`). */
const BOUNDS_CODES: ReadonlySet<string> = new Set([
  'naive_datetime',
  'not_whole_minutes',
  'ends_before_start',
  'duration_out_of_range',
  'crosses_local_date',
]);

export type SendRefusal =
  | {
      kind: 'message';
      code: RefusalCode;
      /** Whole minutes to wait (a 429). */
      minutes?: number;
      /** What the dialog refreshes. */
      refetch?: 'slots' | 'schedule' | 'pending';
    }
  /** A bounds 422 shown on the tutor's time field. */
  | { kind: 'field'; code: ErrorMessageKey };

const MESSAGE_CODES: ReadonlySet<string> = new Set([
  'slot_unavailable',
  'change_request_pending',
  'change_request_session_started',
  'class_session_not_changeable',
  'change_request_kind_unsupported',
  'student_consent_required',
]);

/** The A4 outcome of a refused send; null for an error the shared
 * `ErrorAlert` shows. */
export function sendRefusal(error: unknown): SendRefusal | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (error.status === 429) {
    return {
      kind: 'message',
      code: 'rate_limited',
      minutes: retryMinutes(error) ?? 1,
    };
  }
  if (error.status === 404) {
    return { kind: 'message', code: 'not_found', refetch: 'schedule' };
  }
  if (MESSAGE_CODES.has(error.code)) {
    const code = error.code as RefusalCode;
    if (code === 'slot_unavailable') {
      return { kind: 'message', code, refetch: 'slots' };
    }
    if (code === 'change_request_pending') {
      return { kind: 'message', code, refetch: 'pending' };
    }
    return { kind: 'message', code };
  }
  if (error.status === 422) {
    const field = error.fieldErrors.find(
      (candidate) => candidate.path.at(-1) === 'proposed_starts_at',
    );
    if (field?.code === 'proposal_in_past') {
      return { kind: 'message', code: 'proposal_in_past' };
    }
    if (field?.code === 'proposal_unchanged') {
      return { kind: 'message', code: 'proposal_unchanged' };
    }
    if (field && BOUNDS_CODES.has(field.code)) {
      return { kind: 'field', code: field.code as ErrorMessageKey };
    }
  }
  return null;
}

// ------------------------------------------------------- B: the list page

export const STATUS_FILTERS: readonly StatusFilter[] = [
  'all',
  'pending',
  'approved',
  'rejected',
  'withdrawn',
];

export interface ListState {
  status: StatusFilter;
  /** A guardian's student filter (null: every student). */
  studentId: string | null;
  /** 1-based. */
  page: number;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The list state from the URL; anything unknown falls back. */
export function parseListState(params: URLSearchParams): ListState {
  const status = params.get('status');
  const student = params.get('student')?.toLowerCase() ?? null;
  const page = Number(params.get('page'));
  return {
    status: STATUS_FILTERS.includes(status as StatusFilter)
      ? (status as StatusFilter)
      : 'all',
    studentId: student && UUID.test(student) ? student : null,
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

export function listSearchParams(state: ListState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.status !== 'all') {
    params.set('status', state.status);
  }
  if (state.studentId) {
    params.set('student', state.studentId);
  }
  if (state.page > 1) {
    params.set('page', String(state.page));
  }
  return params;
}

/** Whether the list shows the request-time outcome ("as requested", #62
 * C5): everything but an approved request. */
export function isAsRequested(item: Pick<ChangeRequestItem, 'status'>) {
  return item.status !== 'approved';
}

/** A withdrawal that can no longer happen (the list is stale): its message
 * key, so the page shows it and refetches. Null otherwise. */
export function staleWithdrawal(
  error: unknown,
):
  | 'changeRequests.list.stale.changed'
  | 'changeRequests.list.stale.notPending'
  | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (error.code === 'version_conflict') {
    return 'changeRequests.list.stale.changed';
  }
  if (error.code === 'change_request_not_pending') {
    return 'changeRequests.list.stale.notPending';
  }
  return null;
}
