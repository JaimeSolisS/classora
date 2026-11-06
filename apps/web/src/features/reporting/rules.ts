import { addDays, addMonths, parseLocalDate } from '@/lib/time';
import type { ReportBasis } from './api';

// The report's range and basis (M7-14b E2, B1): presets plus a custom
// range, all in the URL (`basis`, `from`, `to`). With no `from`/`to` the
// range is the page's default preset ("Este mes" for the financial report,
// "Últimos 3 meses" for the KPIs, M8-09b B1) on the anchor date, which is the center's today from
// the last response (the session's center zone before the first one), so
// the default and the presets re-anchor once the API answers. The client
// bounds mirror the server's (#117 E2): `from ≤ to ≤ today`, at most 366
// days (`to − from < 366`); a range that breaks one asks nothing.

export const BASES: readonly ReportBasis[] = ['accrual', 'cash'];
export const DEFAULT_BASIS: ReportBasis = 'accrual';
/** Days in the longest range, both ends included (#117 E2). */
export const MAX_RANGE_DAYS = 366;

export const PRESETS = [
  'thisMonth',
  'lastMonth',
  'last3Months',
  'yearToDate',
] as const;
export type Preset = (typeof PRESETS)[number];

export interface DateRange {
  from: string;
  to: string;
}

export interface ReportParams extends DateRange {
  basis: ReportBasis;
  /** `from`/`to` came from the URL (else the default preset). */
  custom: boolean;
}

export type RangeField = 'from' | 'to';
export type RangeProblem = 'required' | 'order' | 'future' | 'tooLong';

export interface RangeError {
  field: RangeField;
  problem: RangeProblem;
}

export function isLocalDate(value: string | null): value is string {
  if (!value) return false;
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** The preset's range on `today` (E2). */
export function presetRange(preset: Preset, today: string): DateRange {
  const monthStart = `${today.slice(0, 8)}01`;
  switch (preset) {
    case 'thisMonth':
      return { from: monthStart, to: today };
    case 'lastMonth': {
      const from = addMonths(monthStart, -1);
      return { from, to: addDays(monthStart, -1) };
    }
    case 'last3Months':
      // 90 days ending today, both included.
      return { from: addDays(today, -89), to: today };
    case 'yearToDate':
      return { from: `${today.slice(0, 4)}-01-01`, to: today };
  }
}

/** The preset whose range is exactly `range` on `today`, if any. */
export function activePreset(range: DateRange, today: string): Preset | null {
  return (
    PRESETS.find((preset) => {
      const candidate = presetRange(preset, today);
      return candidate.from === range.from && candidate.to === range.to;
    }) ?? null
  );
}

/** The URL's basis and range; a missing or malformed date is the default. */
export function reportParams(
  search: URLSearchParams,
  today: string,
  defaultPreset: Preset = 'thisMonth',
): ReportParams {
  const basisParam = search.get('basis');
  const basis = BASES.find((item) => item === basisParam) ?? DEFAULT_BASIS;
  const from = search.get('from');
  const to = search.get('to');
  if (isLocalDate(from) && isLocalDate(to)) {
    return { basis, from, to, custom: true };
  }
  return { basis, ...presetRange(defaultPreset, today), custom: false };
}

/** Whole days from `from` to `to` (`to − from`). */
function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split('-').map(Number) as [number, number, number];
  const [ty, tm, td] = to.split('-').map(Number) as [number, number, number];
  return Math.round(
    (Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86_400_000,
  );
}

/**
 * The first broken bound, in the server's order (#117 B2): `from > to` (at
 * `to`), `to` after today (at `to`), 366 days or more apart (at `from`).
 * An empty or partial date is `required` at its own field.
 */
export function rangeError(range: DateRange, today: string): RangeError | null {
  if (!isLocalDate(range.from)) return { field: 'from', problem: 'required' };
  if (!isLocalDate(range.to)) return { field: 'to', problem: 'required' };
  if (range.from > range.to) return { field: 'to', problem: 'order' };
  if (range.to > today) return { field: 'to', problem: 'future' };
  if (daysBetween(range.from, range.to) >= MAX_RANGE_DAYS) {
    return { field: 'from', problem: 'tooLong' };
  }
  return null;
}

/** Whether `amount` (a decimal string) is below zero ("-0.00" is not). */
export function isNegative(amount: string): boolean {
  return amount.startsWith('-') && /[1-9]/.test(amount);
}
