// Pure helpers of the weekly windows editor (#39 D3; reused by #45 for tutor
// availability): rows from and to the API's windows, the client-side checks
// that mirror the API (HH:MM, end after start, no overlap with an earlier
// window of the same weekday; touching allowed; at most 28 windows), the
// "did the windows change" test and the one-line summary. UX only: the API
// validates every request.

/** A window as the API sends and takes it (local wall-clock times). */
export interface WeeklyWindow {
  /** ISO weekday: 1 = Monday … 7 = Sunday. */
  weekday: number;
  /** `HH:MM`. */
  starts_at: string;
  /** `HH:MM`, after `starts_at` (same day). */
  ends_at: string;
}

/** One editable row: a window plus a stable key for React. */
export interface WindowRow {
  key: string;
  weekday: number;
  /** `HH:MM`, or empty while not typed. */
  startsAt: string;
  endsAt: string;
}

export type WindowMessageKey =
  | 'weeklyWindows.validation.timeRequired'
  | 'weeklyWindows.validation.endsAfterStart'
  | 'weeklyWindows.validation.overlap'
  | 'weeklyWindows.validation.invalid';

/** The errors of one row, as message keys. */
export interface WindowRowErrors {
  weekday?: WindowMessageKey;
  startsAt?: WindowMessageKey;
  endsAt?: WindowMessageKey;
}

/** The API's limit (models.CLASSROOM_WINDOWS_MAX). */
export const WINDOWS_MAX = 28;
/** The time inputs' step: 15 minutes, in seconds. */
export const WINDOW_TIME_STEP_SECONDS = 900;
/** The ISO weekdays, Monday (1) to Sunday (7), in display order. */
export const WINDOW_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
export type WindowWeekday = (typeof WINDOW_WEEKDAYS)[number];

const LOCAL_TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

let nextKey = 0;

/** A key no other row of this page has. */
export function newRowKey(): string {
  nextKey += 1;
  return `window-${nextKey}`;
}

/** The rows of the stored windows (in their stored order). */
export function rowsOf(windows: readonly WeeklyWindow[]): WindowRow[] {
  return windows.map((window) => ({
    key: newRowKey(),
    weekday: window.weekday,
    startsAt: window.starts_at,
    endsAt: window.ends_at,
  }));
}

/** The windows of valid rows, in row order (the API reports errors by index). */
export function windowsOf(rows: readonly WindowRow[]): WeeklyWindow[] {
  return rows.map((row) => ({
    weekday: row.weekday,
    starts_at: row.startsAt,
    ends_at: row.endsAt,
  }));
}

/**
 * The errors of each row (an empty object when the row is valid), as the
 * API checks them: both times `HH:MM`, the end after the start, and no
 * overlap with an earlier row of the same weekday (the error goes on the
 * later row's start, as the API reports it). Touching windows are fine.
 */
export function validateWindows(rows: readonly WindowRow[]): WindowRowErrors[] {
  return rows.map((row, index) => {
    const errors: WindowRowErrors = {};
    const startOk = LOCAL_TIME.test(row.startsAt);
    const endOk = LOCAL_TIME.test(row.endsAt);
    if (!startOk) {
      errors.startsAt = 'weeklyWindows.validation.timeRequired';
    }
    if (!endOk) {
      errors.endsAt = 'weeklyWindows.validation.timeRequired';
    }
    if (!startOk || !endOk) {
      return errors;
    }
    // HH:MM strings compare like the times they hold.
    if (row.endsAt <= row.startsAt) {
      errors.endsAt = 'weeklyWindows.validation.endsAfterStart';
      return errors;
    }
    const overlaps = rows
      .slice(0, index)
      .some(
        (other) =>
          other.weekday === row.weekday &&
          LOCAL_TIME.test(other.startsAt) &&
          LOCAL_TIME.test(other.endsAt) &&
          other.startsAt < other.endsAt &&
          row.startsAt < other.endsAt &&
          other.startsAt < row.endsAt,
      );
    if (overlaps) {
      errors.startsAt = 'weeklyWindows.validation.overlap';
    }
    return errors;
  });
}

/** Whether any row has an error. */
export function hasWindowErrors(errors: readonly WindowRowErrors[]): boolean {
  return errors.some((row) => Object.keys(row).length > 0);
}

function sortKey(window: WeeklyWindow): string {
  return `${window.weekday} ${window.starts_at} ${window.ends_at}`;
}

/** Whether two lists hold the same windows, whatever their order. */
export function sameWindows(
  a: readonly WeeklyWindow[],
  b: readonly WeeklyWindow[],
): boolean {
  const left = [...new Set(a.map(sortKey))].sort();
  const right = [...new Set(b.map(sortKey))].sort();
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

/** What a summary is made of, before translation. */
export interface SummaryGroup {
  /** Consecutive weekdays with the same windows. */
  firstWeekday: WindowWeekday;
  lastWeekday: WindowWeekday;
  /** `[start, end]` pairs, by start. */
  ranges: [string, string][];
}

/**
 * The windows grouped for a summary: consecutive weekdays with exactly the
 * same windows form one group ("Mon–Fri 09:00–20:00"). Empty when there is
 * no window (the room has no time restriction).
 */
export function summaryGroups(
  windows: readonly WeeklyWindow[],
): SummaryGroup[] {
  const byDay = new Map<number, [string, string][]>();
  for (const window of [...windows].sort((a, b) =>
    sortKey(a) < sortKey(b) ? -1 : 1,
  )) {
    const ranges = byDay.get(window.weekday) ?? [];
    ranges.push([window.starts_at, window.ends_at]);
    byDay.set(window.weekday, ranges);
  }
  const groups: SummaryGroup[] = [];
  for (const weekday of WINDOW_WEEKDAYS) {
    const ranges = byDay.get(weekday);
    if (!ranges) {
      continue;
    }
    const last = groups.at(-1);
    if (
      last &&
      last.lastWeekday === weekday - 1 &&
      JSON.stringify(last.ranges) === JSON.stringify(ranges)
    ) {
      last.lastWeekday = weekday;
    } else {
      groups.push({ firstWeekday: weekday, lastWeekday: weekday, ranges });
    }
  }
  return groups;
}

/** The DOM id of a row's field (`id` is the editor's `id` prop). */
export function windowFieldId(
  id: string,
  rowKey: string,
  field: 'weekday' | 'startsAt' | 'endsAt',
): string {
  return `${id}-${rowKey}-${field}`;
}

/** The DOM id of the editor's "Add window" button. */
export function addWindowId(id: string): string {
  return `${id}-add`;
}
