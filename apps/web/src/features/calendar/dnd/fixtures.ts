import { ApiError } from '@/app/api';
import type { SchedulingConflict } from '@/features/scheduling/create/api';
import type { CalendarItem, ClassSessionUpdated } from '../api';
import { ANA, CARMEN, item, ROOM } from '../fixtures';

// Sample moves for the rescheduling tests and stories (#58 G7, G8): the
// answers of PATCH /api/v1/class-sessions/{id}. Times are in the fixture
// week (Monday 2026-11-09, America/Mexico_City, UTC-6).

export const SOFT_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'outside_operating_hours',
    severity: 'soft',
    details: { date: '2026-11-15', opens_at: null, closes_at: null },
  },
];

export const HARD_CONFLICTS: SchedulingConflict[] = [
  {
    code: 'student_overlap',
    severity: 'hard',
    details: {
      student_id: ANA.student_id,
      class_session_ids: ['00000000-0000-4000-8000-0000000058a1'],
    },
  },
  {
    code: 'outside_operating_hours',
    severity: 'soft',
    details: { date: '2026-11-15', opens_at: null, closes_at: null },
  },
];

function problem(status: number, code: string, extra: object = {}) {
  return {
    type: 'about:blank' as const,
    title: 'Problem',
    status,
    code,
    request_id: `req-${code}`,
    ...extra,
  };
}

export function conflictError(
  conflicts: SchedulingConflict[] = SOFT_CONFLICTS,
): ApiError {
  return new ApiError({
    code: 'scheduling_conflict',
    status: 409,
    requestId: 'req-scheduling_conflict',
    problem: problem(409, 'scheduling_conflict', { conflicts }),
  });
}

export function staleError(): ApiError {
  return new ApiError({
    code: 'version_conflict',
    status: 409,
    requestId: 'req-version_conflict',
    problem: problem(409, 'version_conflict'),
  });
}

export function notEditableError(): ApiError {
  return new ApiError({
    code: 'session_not_editable',
    status: 409,
    requestId: 'req-session_not_editable',
    problem: problem(409, 'session_not_editable'),
  });
}

export function notFoundError(): ApiError {
  return new ApiError({ code: 'not_found', status: 404, requestId: 'req-404' });
}

export function forbiddenError(): ApiError {
  return new ApiError({
    code: 'permission_denied',
    status: 403,
    requestId: 'req-403',
  });
}

export function invalidError(
  code = 'crosses_local_date',
  path: (string | number)[] = ['body', 'ends_at'],
): ApiError {
  const errors = [{ path, code, message: code }];
  return new ApiError({
    code: 'validation_error',
    status: 422,
    requestId: 'req-422',
    fieldErrors: errors,
    problem: problem(422, 'validation_error', { errors }),
  });
}

export function serverError(): ApiError {
  return new ApiError({
    code: 'internal_error',
    status: 500,
    requestId: 'req-500',
  });
}

/** An editable one-off session on Monday 10:00–11:00 (version 3). */
export const MOVABLE: CalendarItem = item({
  id: '00000000-0000-4000-8000-0000000058b1',
  state: 'unassigned',
  version: 3,
  tutor: null,
  starts_at: '2026-11-09T16:00:00Z',
  ends_at: '2026-11-09T17:00:00Z',
});

/** An occurrence of a weekly series, confirmed, Tuesday 10:00–11:00. */
export const OCCURRENCE: CalendarItem = item({
  id: '00000000-0000-4000-8000-0000000058b2',
  state: 'confirmed',
  version: 2,
  series_id: '00000000-0000-4000-8000-0000000058c1',
  starts_at: '2026-11-10T16:00:00Z',
  ends_at: '2026-11-10T17:00:00Z',
});

/** A completed session: never movable. */
export const FIXED: CalendarItem = item({
  id: '00000000-0000-4000-8000-0000000058b3',
  state: 'completed',
  starts_at: '2026-11-11T16:00:00Z',
  ends_at: '2026-11-11T17:00:00Z',
});

/** A session moved with an override earlier. */
export const OVERRIDDEN_ITEM: CalendarItem = item({
  id: '00000000-0000-4000-8000-0000000058b4',
  state: 'unassigned',
  tutor: null,
  starts_at: '2026-11-15T16:00:00Z',
  ends_at: '2026-11-15T17:00:00Z',
  markers: ['overridden'],
});

/** The 200 of a move of `source` to the slot (version + 1). */
export function updated(
  source: CalendarItem,
  slot: { starts_at: string; ends_at: string },
  overrides: Partial<ClassSessionUpdated> = {},
): ClassSessionUpdated {
  return {
    id: source.id,
    kind: source.kind,
    state: source.state,
    subject: { ...source.subject, is_active: true, education_level_id: 'x' },
    tutor: source.tutor
      ? { ...CARMEN, ...source.tutor, is_active: true }
      : null,
    classroom: source.classroom
      ? { ...ROOM, ...source.classroom, is_active: true }
      : null,
    participants: [],
    starts_at: slot.starts_at,
    ends_at: slot.ends_at,
    scheduled_minutes:
      (Date.parse(slot.ends_at) - Date.parse(slot.starts_at)) / 60_000,
    version: source.version + 1,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
    overrides: [],
    series_id: source.series_id,
    original_start: source.series_id ? source.starts_at : null,
    is_exception: source.series_id !== null,
    conflict_codes: [],
    pending_offer: null,
    pending_substitution: null,
    cancellation: null,
    group_id: null,
    warnings: [],
    ...overrides,
  } as ClassSessionUpdated;
}
