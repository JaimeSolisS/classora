import type { components } from '@classora/api-client';
import type { SchedulingConflict } from '../create/api';
import { conflictsByDate } from '../session/rules';
import type { ClassSeriesCreate, SeriesOccurrence } from './api';

// Series overrides (#236 H1-H3), without React. A series write overrides
// its soft conflicts occurrence by occurrence: the dialog sends one pair
// for every soft (details.occurrence_start, code) the 409 listed, with the
// instants exactly as the server wrote them (never rebuilt from local
// time), plus the reason. A `conflict` row of the series page overrides
// its own codes through POST /class-sessions/{id}/conflict-overrides.

export type SeriesOverride = NonNullable<ClassSeriesCreate['override']>;
export type OverridePair = components['schemas']['OverridePair'];
export type ConflictOverrideCreate =
  components['schemas']['ConflictOverrideCreate'];

/** The ADR-0005 soft codes: the only ones an override may name. */
export const SOFT_CODES: ReadonlySet<string> = new Set([
  'tutor_unavailable',
  'room_unavailable',
  'center_closed',
  'outside_operating_hours',
  'tutor_not_qualified',
  'group_capacity_exceeded',
  'notice_window',
]);

function occurrenceStart(conflict: SchedulingConflict): string | null {
  const start = conflict.details.occurrence_start;
  return typeof start === 'string' ? start : null;
}

/**
 * One pair for every soft conflict of the 409 (in its order, each once),
 * with `details.occurrence_start` exactly as sent.
 */
export function seriesOverridePairs(
  conflicts: readonly SchedulingConflict[],
): OverridePair[] {
  const seen = new Set<string>();
  const pairs: OverridePair[] = [];
  for (const conflict of conflicts) {
    const start = occurrenceStart(conflict);
    const key = `${start ?? ''} ${conflict.code}`;
    if (conflict.severity !== 'soft' || start === null || seen.has(key)) {
      continue;
    }
    seen.add(key);
    pairs.push({ occurrence_start: start, code: conflict.code });
  }
  return pairs;
}

/** The `override` member of a series create or edit: every soft pair. */
export function seriesOverride(
  conflicts: readonly SchedulingConflict[],
  reason: string,
): SeriesOverride {
  return { pairs: seriesOverridePairs(conflicts), reason: reason.trim() };
}

/** The soft conflicts by local date (the dialog's read-only list). */
export function softConflictsByDate(
  conflicts: readonly SchedulingConflict[],
): { date: string; conflicts: SchedulingConflict[] }[] {
  return conflictsByDate(conflicts.filter((c) => c.severity === 'soft'));
}

/** How many occurrences the soft conflicts affect. */
export function affectedOccurrences(
  conflicts: readonly SchedulingConflict[],
): number {
  return new Set(
    conflicts
      .filter((c) => c.severity === 'soft')
      .map((c) => occurrenceStart(c) ?? `${String(c.details.date)}`),
  ).size;
}

/** How many of the series' rows carry an override (the success line). */
export function overriddenCount(
  occurrences: readonly Pick<SeriesOccurrence, 'overridden'>[],
): number {
  return occurrences.filter((o) => o.overridden).length;
}

/**
 * What a `conflict` row of the series page offers (H3): "override" when
 * the user holds schedule.manage and schedule.override, the row hasn't
 * started and every code is soft; "fix" when a code is hard or a data
 * validity code (never overridable); nothing otherwise.
 */
export function conflictRowAction(
  occurrence: Pick<SeriesOccurrence, 'state' | 'starts_at' | 'conflict_codes'>,
  permissions: { canManage: boolean; canOverride: boolean },
  now: number = Date.now(),
): 'override' | 'fix' | null {
  if (
    occurrence.state !== 'conflict' ||
    !permissions.canManage ||
    !permissions.canOverride ||
    Date.parse(occurrence.starts_at) <= now
  ) {
    return null;
  }
  const codes = occurrence.conflict_codes;
  return codes.length > 0 && codes.every((code) => SOFT_CODES.has(code))
    ? 'override'
    : 'fix';
}

/** The G3 body: the row's own codes and the reason, with its version. */
export function conflictOverrideBody(
  occurrence: Pick<SeriesOccurrence, 'conflict_codes' | 'version'>,
  reason: string,
): ConflictOverrideCreate {
  return {
    version: occurrence.version,
    override: {
      codes: occurrence.conflict_codes.filter((code) => SOFT_CODES.has(code)),
      reason: reason.trim(),
    },
  };
}
