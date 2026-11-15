import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import { FIELD_ERROR_CODES } from '@/features/scheduling/create/rules';
import type { SchedulingConflict } from '@/features/scheduling/create/api';
import {
  sessionOverride,
  type SessionOverride,
} from '@/features/scheduling/create/rules';
import { localDateTimeToInstant, toLocalDate, toLocalTime } from '@/lib/time';
import type { CalendarItem, ClassSessionUpdate, SessionState } from '../api';

// The rules of rescheduling on the calendar (#58 D, E, F), without React:
// which events can move, the PATCH body of a drop, a resize or the
// Reschedule dialog, and what a refusal means for the calendar. The server
// is the authority on every scheduling rule; nothing here predicts a
// conflict.

/** The states a session can be moved in (mirrors #54 `session_not_editable`). */
export const EDITABLE_STATES: ReadonlySet<SessionState> = new Set([
  'unassigned',
  'offered',
  'confirmed',
  'conflict',
]);

/**
 * Whether the user can drag, resize or reschedule the event (#58 D3): a
 * holder of schedule.manage, an editable state, and not started yet
 * (`starts_at > now`).
 */
export function isEditable(
  item: Pick<CalendarItem, 'state' | 'starts_at'>,
  now: number,
  canManage: boolean,
): boolean {
  return (
    canManage &&
    EDITABLE_STATES.has(item.state) &&
    Date.parse(item.starts_at) > now
  );
}

/** The new times of a moved event, as instants. */
export interface MovedSlot {
  starts_at: string;
  ends_at: string;
}

/**
 * The slot of a dropped or resized event: FullCalendar gives each edge as a
 * real instant (a Date; its `timeZone` is the center zone), so nothing here
 * reads the browser zone. Null when an edge is missing.
 */
export function droppedSlot(
  start: Date | null,
  end: Date | null,
): MovedSlot | null {
  if (!start || !end) {
    return null;
  }
  return { starts_at: start.toISOString(), ends_at: end.toISOString() };
}

/**
 * The PATCH body of a move (#58 D5): the item's version and the new
 * instants, never `classroom_id` (the room is unchanged). Null when the
 * slot is the item's own (a drop onto the same place sends nothing).
 */
export function moveBody(
  item: Pick<CalendarItem, 'version' | 'starts_at' | 'ends_at'>,
  slot: MovedSlot,
): ClassSessionUpdate | null {
  if (
    Date.parse(slot.starts_at) === Date.parse(item.starts_at) &&
    Date.parse(slot.ends_at) === Date.parse(item.ends_at)
  ) {
    return null;
  }
  return {
    version: item.version,
    starts_at: slot.starts_at,
    ends_at: slot.ends_at,
  };
}

/**
 * The resend after "Override and move" (#58 F3): the same body and version,
 * with every soft conflict shown and the trimmed reason.
 */
export function overrideBody(
  body: ClassSessionUpdate,
  conflicts: readonly SchedulingConflict[],
  reason: string,
): ClassSessionUpdate & { override: SessionOverride } {
  return { ...body, override: sessionOverride(conflicts, reason) };
}

/**
 * What a refused move means for the calendar (#58 D6):
 * - `conflict`: a 409 scheduling_conflict (the "Move not saved" dialog);
 * - `stale`: a 409 version_conflict (reload, no retry);
 * - `gone`: the session can't be moved any more (session_not_editable) or
 *   no longer exists (404): a message and the range is read again;
 * - `invalid`: a 422 (a field message);
 * - `other`: anything else (403, network, 5xx): the shared ErrorAlert.
 */
export type RefusalKind = 'conflict' | 'stale' | 'gone' | 'invalid' | 'other';

export function refusalKind(error: unknown): RefusalKind {
  if (!(error instanceof ApiError)) {
    return 'other';
  }
  if (error.code === 'scheduling_conflict') {
    return 'conflict';
  }
  if (error.code === 'version_conflict') {
    return 'stale';
  }
  if (error.code === 'session_not_editable' || error.status === 404) {
    return 'gone';
  }
  return error.status === 422 ? 'invalid' : 'other';
}

/**
 * The `errors` key of a refused drop's 422 (the first field error the
 * catalog translates: crosses_local_date, duration_out_of_range,
 * session_unchanged, …), or validation_error.
 */
export function invalidMoveMessage(error: unknown): ErrorMessageKey {
  if (error instanceof ApiError) {
    for (const { code } of error.fieldErrors) {
      if (FIELD_ERROR_CODES.has(code) || code === 'session_unchanged') {
        return code as ErrorMessageKey;
      }
    }
  }
  return 'validation_error';
}

// --- The Reschedule dialog (#58 E) --------------------------------------------

export interface RescheduleValues {
  /** YYYY-MM-DD in the center zone. */
  date: string;
  /** HH:MM in the center zone. */
  start: string;
  end: string;
}

/** The event's local date and times in the center zone. */
export function rescheduleValues(
  item: Pick<CalendarItem, 'starts_at' | 'ends_at'>,
  timeZone: string,
): RescheduleValues {
  return {
    date: toLocalDate(item.starts_at, timeZone),
    start: toLocalTime(item.starts_at, timeZone),
    end: toLocalTime(item.ends_at, timeZone),
  };
}

/** The dialog's slot as instants in the center zone (`lib/time`). */
export function rescheduleSlot(
  values: RescheduleValues,
  timeZone: string,
): MovedSlot {
  return {
    starts_at: localDateTimeToInstant(values.date, values.start, timeZone),
    ends_at: localDateTimeToInstant(values.date, values.end, timeZone),
  };
}
