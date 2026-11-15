import type { TFunction } from 'i18next';

// Package units (#94, #377 B1): a sessions package counts sessions, a
// minutes package counts minutes (in steps of 15) and is shown as hours and
// minutes ("20 h", "1 h 15 min", "45 min"). The quantity field of an hours
// package is typed in hours (0.25 steps) and sent as minutes.

export type PackageUnit = 'sessions' | 'minutes';

/** The API's limits (#94 B2): 1-500 sessions, 15-30000 minutes. */
export const SESSIONS_MIN = 1;
export const SESSIONS_MAX = 500;
export const MINUTES_MIN = 15;
export const MINUTES_MAX = 30000;
export const MINUTES_STEP = 15;
/** 1-730 days. */
export const VALIDITY_MIN = 1;
export const VALIDITY_MAX = 730;

/** Hours and minutes of a non-negative number of minutes. */
function hoursAndMinutes(minutes: number): { hours: number; rest: number } {
  const whole = Math.abs(Math.trunc(minutes));
  return { hours: Math.floor(whole / 60), rest: whole % 60 };
}

/**
 * The units as words: "10 sessions", "1 session", "20 h", "1 h 15 min",
 * "45 min", "0 min". Negative values are shown by their size (callers add
 * the sign).
 */
export function formatPackageUnits(
  unit: PackageUnit,
  value: number,
  t: TFunction,
): string {
  const size = Math.abs(Math.trunc(value));
  if (unit === 'sessions') {
    return t('billing.packages.units.sessions', { count: size });
  }
  const { hours, rest } = hoursAndMinutes(size);
  if (hours === 0) {
    return t('billing.packages.units.minutes', { minutes: rest });
  }
  if (rest === 0) {
    return t('billing.packages.units.hours', { hours });
  }
  return t('billing.packages.units.hoursMinutes', { hours, minutes: rest });
}

/** A signed movement: "+10 sessions", "−1 session", "−1 h 30 min". */
export function formatSignedUnits(
  unit: PackageUnit,
  value: number,
  t: TFunction,
): string {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${formatPackageUnits(unit, value, t)}`;
}

/** Remaining of granted: "8 / 10 sessions", "1 h 15 min / 20 h". */
export function formatPackageBalance(
  unit: PackageUnit,
  remaining: number,
  granted: number,
  t: TFunction,
): string {
  if (unit === 'sessions') {
    return t('billing.packages.units.sessionsBalance', {
      remaining,
      count: granted,
    });
  }
  return t('billing.packages.units.balance', {
    remaining: formatPackageUnits(unit, remaining, t),
    granted: formatPackageUnits(unit, granted, t),
  });
}

const HOURS_INPUT = /^\d{1,5}(?:[.,]\d{1,2})?$/;

/**
 * The minutes of an hours input ("1.25" or "1,25" → 75), or null when it
 * isn't a number of hours in 0.25 steps.
 */
export function hoursInputToMinutes(value: string): number | null {
  const text = value.trim();
  if (!HOURS_INPUT.test(text)) {
    return null;
  }
  const [whole = '0', fraction = ''] = text.replace(',', '.').split('.');
  const hundredths = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  // 0.25 h = 25 hundredths = 15 minutes.
  if (hundredths % 25 !== 0) {
    return null;
  }
  return (hundredths / 25) * MINUTES_STEP;
}
