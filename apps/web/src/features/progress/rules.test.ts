import { describe, expect, it } from 'vitest';
import { ApiError } from '@/app/api';
import { parseLocalDate, todayInZone } from '@/lib/time';
import { MATH, MUSIC, PHYSICS, PROGRESS } from './fixtures';
import {
  hasProgressFilters,
  monthLabel,
  parseProgressControls,
  rangeErrorOf,
  rangeFor,
  subjectOptions,
  withProgressControls,
  type MonthPreset,
} from './rules';

// #322 B2, B3, D1 (pure): the presets' ranges in the center zone (a month
// boundary, a year boundary, the 31st), the controls in the URL, the
// subject options (AM1), month labels without the device zone, and the
// range and subject refusals read from the field codes.

const day = (value: string) => parseLocalDate(value);
const ZONE = 'America/Mexico_City';

describe('rangeFor (B2)', () => {
  it.each<[MonthPreset, string, string]>([
    [3, '2026-03-31', '2026-01-01'],
    [6, '2026-03-31', '2025-10-01'],
    [12, '2026-03-31', '2025-04-01'],
    [24, '2026-03-31', '2024-04-01'],
    [3, '2026-01-01', '2025-11-01'],
    [12, '2026-01-15', '2025-02-01'],
    [24, '2026-02-28', '2024-03-01'],
  ])('%s months up to %s start on %s', (months, today, from) => {
    expect(rangeFor(months, day(today))).toEqual({ from, to: today });
  });

  it("uses the center's today, not UTC: 03:00Z on Nov 1 is Oct 31 in Mexico City", () => {
    const today = todayInZone(ZONE, new Date('2026-11-01T03:00:00Z'));
    expect(today).toBe('2026-10-31');
    expect(rangeFor(3, today)).toEqual({
      from: '2026-08-01',
      to: '2026-10-31',
    });
  });
});

describe('the controls in the URL (B2, B3)', () => {
  const parse = (query: string) =>
    parseProgressControls(new URLSearchParams(query));

  it('defaults to 12 months and every subject', () => {
    expect(parse('')).toEqual({ months: 12, subject: null });
  });

  it.each(['3', '6', '12', '24'])('reads months=%s', (months) => {
    expect(parse(`months=${months}`).months).toBe(Number(months));
  });

  it.each(['0', '5', '36', 'abc', '-3', '12.0x'])(
    'an invalid months=%s falls back to 12',
    (months) => {
      expect(parse(`months=${months}`).months).toBe(12);
    },
  );

  it('reads a subject id; anything else is ignored', () => {
    expect(parse(`subject=${MATH.id}`).subject).toBe(MATH.id);
    expect(parse('subject=maths').subject).toBeNull();
    expect(parse('subject=').subject).toBeNull();
  });

  it('writes only what differs from the default and keeps ?student=', () => {
    const params = new URLSearchParams('student=abc&months=6&subject=x');
    expect(
      withProgressControls(params, { months: 12, subject: null }).toString(),
    ).toBe('student=abc');
    expect(
      withProgressControls(params, { months: 3, subject: MATH.id }).toString(),
    ).toBe(`student=abc&months=3&subject=${MATH.id}`);
  });

  it('"Clear filters" shows for a preset or a subject', () => {
    expect(hasProgressFilters({ months: 12, subject: null })).toBe(false);
    expect(hasProgressFilters({ months: 6, subject: null })).toBe(true);
    expect(hasProgressFilters({ months: 12, subject: MATH.id })).toBe(true);
  });
});

describe('subjectOptions (B3, AM1)', () => {
  it("lists the unfiltered range's subjects, a class-only subject included", () => {
    expect(subjectOptions(PROGRESS)).toEqual([PHYSICS, MATH, MUSIC]);
  });

  it('keeps the filtered subject even when it is not among them', () => {
    const other = { id: '00000000-0000-4000-8000-000000322a99', name: 'Arte' };
    expect(
      subjectOptions({ ...PROGRESS, subject: other }).map((s) => s.name),
    ).toEqual(['Física', 'Matemáticas', 'Música', 'Arte']);
  });

  it('none before the summary answers', () => {
    expect(subjectOptions(undefined)).toEqual([]);
  });
});

describe('monthLabel', () => {
  it.each([
    ['2026-03', 'en', 'Mar 2026'],
    ['2026-01', 'en', 'Jan 2026'],
    ['2026-12', 'es', 'dic 2026'],
  ] as const)('%s in %s is %s', (month, language, label) => {
    expect(monthLabel(month, language)).toBe(label);
  });

  it('an unexpected value is shown as sent', () => {
    expect(monthLabel('2026', 'en')).toBe('2026');
  });
});

describe('rangeErrorOf (D1)', () => {
  const field = (code: string, at: string) =>
    new ApiError({
      code: 'validation_error',
      status: 422,
      fieldErrors: [{ path: ['query', at], code, message: 'x' }],
    });

  it.each([
    ['range_invalid', 'from'],
    ['range_in_future', 'to'],
    ['range_too_long', 'from'],
    ['subject_unknown', 'subject_id'],
  ])('reads the field code %s', (code, at) => {
    expect(rangeErrorOf(field(code, at))).toBe(code);
  });

  it('reads a top-level code too', () => {
    expect(
      rangeErrorOf(new ApiError({ code: 'range_in_future', status: 422 })),
    ).toBe('range_in_future');
  });

  it('other failures are not range refusals', () => {
    expect(rangeErrorOf(field('too_long', 'from'))).toBeNull();
    expect(
      rangeErrorOf(new ApiError({ code: 'not_found', status: 404 })),
    ).toBeNull();
    expect(rangeErrorOf(new Error('x'))).toBeNull();
  });
});
