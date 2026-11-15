import { addDays, addMonths, type LocalDate } from './time';

// The tuition period grid (#93 K3, #374 D1), mirroring the API's
// app/modules/billing/tuition.py: a plan's periods are anchored at its
// `starts_on` and always whole. Weekly periods are 7 days, biweekly 14;
// monthly ones start `starts_on` plus k calendar months with the day
// clamped, always computed from `starts_on` (31 Jan → 28 Feb → 31 Mar).
// A period ends the day before the next one starts. No zone is involved:
// these are the center's local dates.

export type PeriodFrequency = 'weekly' | 'biweekly' | 'monthly';

export const PERIOD_FREQUENCIES: readonly PeriodFrequency[] = [
  'weekly',
  'biweekly',
  'monthly',
];

const DAYS: Record<Exclude<PeriodFrequency, 'monthly'>, number> = {
  weekly: 7,
  biweekly: 14,
};

/** The first day of period `k` (0-based) of a plan from `startsOn`. */
export function tuitionPeriodStart(
  startsOn: LocalDate | string,
  frequency: PeriodFrequency,
  k: number,
): LocalDate {
  if (!Number.isInteger(k) || k < 0) {
    throw new RangeError('period indexes are whole numbers from 0');
  }
  return frequency === 'monthly'
    ? addMonths(startsOn, k)
    : addDays(startsOn, DAYS[frequency] * k);
}

/**
 * The last day of a plan that runs `n` whole periods from `startsOn`: the
 * `ends_on` a create sends for "After N periods" (the day before period
 * `n` starts).
 */
export function tuitionPeriodEnd(
  startsOn: LocalDate | string,
  frequency: PeriodFrequency,
  n: number,
): LocalDate {
  if (!Number.isInteger(n) || n < 1) {
    throw new RangeError('a plan runs at least one period');
  }
  return addDays(tuitionPeriodStart(startsOn, frequency, n), -1);
}
