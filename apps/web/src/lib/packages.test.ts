import { beforeEach, describe, expect, it } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import {
  formatPackageBalance,
  formatPackageUnits,
  formatSignedUnits,
  hoursInputToMinutes,
} from './packages';

// #377 B1, G1: package units as words (sessions; minutes as hours and
// minutes) and the hours input of an hours package.

const t = i18n.t.bind(i18n);

describe('formatPackageUnits', () => {
  beforeEach(() => {
    applyLanguage('en');
  });

  it.each([
    ['sessions', 10, '10 sessions'],
    ['sessions', 1, '1 session'],
    ['minutes', 90, '1 h 30 min'],
    ['minutes', 1200, '20 h'],
    ['minutes', 75, '1 h 15 min'],
    ['minutes', 45, '45 min'],
    ['minutes', 0, '0 min'],
  ] as const)('%s %d → %s', (unit, value, expected) => {
    expect(formatPackageUnits(unit, value, t)).toBe(expected);
  });

  it('follows the language', () => {
    applyLanguage('es');
    expect(formatPackageUnits('sessions', 10, t)).toBe('10 sesiones');
    expect(formatPackageUnits('sessions', 1, t)).toBe('1 sesión');
    expect(formatPackageUnits('minutes', 75, t)).toBe('1 h 15 min');
  });

  it('signs a movement and shows a balance', () => {
    expect(formatSignedUnits('sessions', 10, t)).toBe('+10 sessions');
    expect(formatSignedUnits('sessions', -1, t)).toBe('−1 session');
    expect(formatSignedUnits('minutes', -90, t)).toBe('−1 h 30 min');
    expect(formatPackageBalance('sessions', 8, 10, t)).toBe('8 / 10 sessions');
    expect(formatPackageBalance('minutes', 75, 1200, t)).toBe(
      '1 h 15 min / 20 h',
    );
  });
});

describe('hoursInputToMinutes', () => {
  it.each([
    ['20', 1200],
    ['1.25', 75],
    ['1,5', 90],
    ['0.25', 15],
    [' 2.50 ', 150],
    ['0', 0],
  ])('%s h → %d min', (input, minutes) => {
    expect(hoursInputToMinutes(input)).toBe(minutes);
  });

  it.each(['', '1.2', '1.333', 'abc', '-1', '1.', '.5', '1e2'])(
    'refuses %j',
    (input) => {
      expect(hoursInputToMinutes(input)).toBeNull();
    },
  );
});
