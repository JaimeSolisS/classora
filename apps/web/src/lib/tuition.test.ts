import { describe, expect, it } from 'vitest';
import { tuitionPeriodEnd, tuitionPeriodStart } from './tuition';

// #374 D1, H1: the web grid mirrors the API's (#93 K3, the H1 cases of
// tests/unit/test_tuition_periods.py).

describe('tuitionPeriodStart', () => {
  it('weekly and biweekly periods are 7 and 14 days from the start', () => {
    expect(tuitionPeriodStart('2027-01-06', 'weekly', 0)).toBe('2027-01-06');
    expect(tuitionPeriodStart('2027-01-06', 'weekly', 3)).toBe('2027-01-27');
    expect(tuitionPeriodStart('2027-01-06', 'biweekly', 2)).toBe('2027-02-03');
  });

  it('monthly periods from the 31st clamp and come back', () => {
    expect(
      [0, 1, 2, 3].map((k) => tuitionPeriodStart('2027-01-31', 'monthly', k)),
    ).toEqual(['2027-01-31', '2027-02-28', '2027-03-31', '2027-04-30']);
  });

  it('monthly from a leap day', () => {
    expect(tuitionPeriodStart('2028-02-29', 'monthly', 1)).toBe('2028-03-29');
    expect(tuitionPeriodStart('2028-02-29', 'monthly', 12)).toBe('2029-02-28');
    expect(tuitionPeriodStart('2028-02-29', 'monthly', 13)).toBe('2029-03-29');
  });

  it('monthly is computed from the start, not the previous period', () => {
    expect(tuitionPeriodStart('2027-01-30', 'monthly', 1)).toBe('2027-02-28');
    expect(tuitionPeriodStart('2027-01-30', 'monthly', 2)).toBe('2027-03-30');
  });

  it('refuses a negative or fractional index', () => {
    expect(() => tuitionPeriodStart('2027-01-06', 'weekly', -1)).toThrow();
    expect(() => tuitionPeriodStart('2027-01-06', 'weekly', 1.5)).toThrow();
  });
});

describe('tuitionPeriodEnd', () => {
  it('ends the day before period n starts', () => {
    expect(tuitionPeriodEnd('2027-01-06', 'weekly', 1)).toBe('2027-01-12');
    expect(tuitionPeriodEnd('2027-01-06', 'biweekly', 1)).toBe('2027-01-19');
    expect(tuitionPeriodEnd('2027-01-06', 'biweekly', 3)).toBe('2027-02-16');
  });

  it('31 January monthly: one period ends 27 Feb, two end 30 Mar', () => {
    expect(tuitionPeriodEnd('2027-01-31', 'monthly', 1)).toBe('2027-02-27');
    expect(tuitionPeriodEnd('2027-01-31', 'monthly', 2)).toBe('2027-03-30');
  });

  it('a leap day monthly plan of 12 periods ends 27 Feb of the next year', () => {
    expect(tuitionPeriodEnd('2028-02-29', 'monthly', 12)).toBe('2029-02-27');
    expect(tuitionPeriodEnd('2028-01-31', 'monthly', 1)).toBe('2028-02-28');
  });

  it('refuses fewer than one period', () => {
    expect(() => tuitionPeriodEnd('2027-01-06', 'monthly', 0)).toThrow();
  });
});
