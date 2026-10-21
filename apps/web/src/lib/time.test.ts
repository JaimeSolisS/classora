import { describe, expect, it } from 'vitest';
import {
  addMonths,
  DEFAULT_CENTER_TIME_ZONE,
  formatUtcOffset,
  hasOffsetChange,
  formatInstant,
  formatLocalDate,
  localDateTimeToInstant,
  localDayBounds,
  parseInstant,
  parseLocalDate,
  todayInZone,
  toLocalDate,
  toLocalTime,
} from './time';

// These tests must pass under any host zone. `make test-web` runs them under
// Pacific/Kiritimati (UTC+14) unless TZ is set; the issue records runs with
// TZ=UTC, TZ=America/Mexico_City and TZ=Pacific/Kiritimati.
const MX = DEFAULT_CENTER_TIME_ZONE;
const TIJUANA = 'America/Tijuana';
const HOUR_MS = 3_600_000;

function hoursBetween(start: string, end: string): number {
  return (Date.parse(end) - Date.parse(start)) / HOUR_MS;
}

describe('host time zone', () => {
  it('is the one given by TZ (non-UTC by default)', () => {
    const expected = import.meta.env.TZ as string | undefined;
    expect(expected).toBeTruthy();
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(expected);
  });
});

describe('DEFAULT_CENTER_TIME_ZONE', () => {
  it('is America/Mexico_City', () => {
    expect(DEFAULT_CENTER_TIME_ZONE).toBe('America/Mexico_City');
  });
});

describe('instant → local values', () => {
  it('gives the 24-hour wall-clock time in the zone (#50), across a DST change', () => {
    expect(toLocalTime('2026-10-18T05:30:00Z', MX)).toBe('23:30');
    // Tijuana 2026-03-08: 01:59 PST, then 03:00 PDT.
    expect(toLocalTime('2026-03-08T09:59:00Z', TIJUANA)).toBe('01:59');
    expect(toLocalTime('2026-03-08T10:00:00Z', TIJUANA)).toBe('03:00');
  });

  it('uses the zone date, not the UTC date, late in the evening', () => {
    expect(toLocalDate('2026-10-18T05:30:00Z', MX)).toBe('2026-10-17');
    expect(
      formatInstant('2026-10-18T05:30:00Z', {
        timeZone: MX,
        language: 'es',
        style: 'time',
      }),
    ).toBe('23:30');
  });

  it('rolls over at local midnight', () => {
    expect(toLocalDate('2026-10-18T06:00:00Z', MX)).toBe('2026-10-18');
    expect(
      formatInstant('2026-10-18T06:00:00Z', {
        timeZone: MX,
        language: 'es',
        style: 'time',
      }),
    ).toBe('00:00');
  });

  it('gives the same values for the same instant in any offset', () => {
    for (const instant of [
      '2026-10-17T23:30:00-06:00',
      '2026-10-18T05:30:00Z',
      '2026-10-18T05:30:00.000+00:00',
    ]) {
      expect(toLocalDate(instant, MX)).toBe('2026-10-17');
      expect(formatInstant(instant, { timeZone: MX, language: 'es' })).toBe(
        '17 oct 2026, 23:30',
      );
    }
  });

  it('applies historic offsets (Mexico City DST, 2022)', () => {
    expect(toLocalDate('2022-07-01T04:30:00Z', MX)).toBe('2022-06-30');
    expect(
      formatInstant('2022-07-01T04:30:00Z', {
        timeZone: MX,
        language: 'es',
        style: 'time',
      }),
    ).toBe('23:30');
  });

  it.each([
    ['es', 'date', '17 oct 2026'],
    ['es', 'time', '23:30'],
    ['es', 'dateTime', '17 oct 2026, 23:30'],
    ['en', 'date', 'Oct 17, 2026'],
    ['en', 'time', '11:30 PM'],
    ['en', 'dateTime', 'Oct 17, 2026, 11:30 PM'],
  ] as const)('formats in %s with style %s', (language, style, expected) => {
    expect(
      formatInstant('2026-10-18T05:30:00Z', { timeZone: MX, language, style }),
    ).toBe(expected);
  });

  it('formats in the zone given, not the host zone', () => {
    expect(
      formatInstant('2026-10-18T05:30:00Z', {
        timeZone: 'UTC',
        language: 'en',
      }),
    ).toBe('Oct 18, 2026, 5:30 AM');
  });
});

describe('host zone independence', () => {
  it('formats a wall time that does not exist in a DST host zone', () => {
    // 02:30 on 2026-03-08 is in the US spring-forward gap. A formatter that
    // rebuilds the wall time in the host zone prints 03:30 under
    // TZ=America/New_York; this one prints 02:30 under every host zone.
    expect(
      formatInstant('2026-03-08T02:30:00Z', {
        timeZone: 'UTC',
        language: 'es',
        style: 'dateTime',
      }),
    ).toBe('8 mar 2026, 02:30');
  });
});

describe('todayInZone', () => {
  const now = new Date('2026-10-18T05:30:00Z');

  it('returns the current local date in the zone', () => {
    expect(todayInZone(MX, now)).toBe('2026-10-17');
    expect(todayInZone('UTC', now)).toBe('2026-10-18');
  });

  it('uses the real clock by default', () => {
    expect(todayInZone(MX)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('calendar dates are never shifted', () => {
  it.each([
    ['es', '1 mar 2026'],
    ['en', 'Mar 1, 2026'],
  ] as const)('formats 2026-03-01 as 1 March in %s', (language, expected) => {
    expect(formatLocalDate('2026-03-01', { language })).toBe(expected);
  });

  it.each([
    ['es', '1 de marzo de 2026'],
    ['en', 'March 1, 2026'],
  ] as const)('formats the long style in %s', (language, expected) => {
    expect(formatLocalDate('2026-03-01', { language, style: 'long' })).toBe(
      expected,
    );
  });

  it('keeps year boundaries and leap days', () => {
    expect(formatLocalDate('2026-01-01', { language: 'en' })).toBe(
      'Jan 1, 2026',
    );
    expect(formatLocalDate('2024-02-29', { language: 'en' })).toBe(
      'Feb 29, 2024',
    );
    expect(formatLocalDate('2026-12-31', { language: 'es' })).toBe(
      '31 dic 2026',
    );
  });
});

describe('local → instant', () => {
  it('converts a wall-clock time in the zone to UTC', () => {
    expect(localDateTimeToInstant('2026-10-17', '23:30', MX)).toBe(
      '2026-10-18T05:30:00.000Z',
    );
  });

  it('uses historic offsets', () => {
    expect(localDateTimeToInstant('2022-06-30', '23:30', MX)).toBe(
      '2022-07-01T04:30:00.000Z',
    );
  });

  it('moves a spring-forward gap time forward by the gap', () => {
    // 2026-03-08 02:30 does not exist in Tijuana (02:00 PST → 03:00 PDT).
    const instant = localDateTimeToInstant('2026-03-08', '02:30', TIJUANA);
    expect(instant).toBe('2026-03-08T10:30:00.000Z');
    expect(
      formatInstant(instant, {
        timeZone: TIJUANA,
        language: 'es',
        style: 'time',
      }),
    ).toBe('03:30');
  });

  it('takes the earlier instant for an ambiguous fall-back time', () => {
    // 2026-11-01 01:30 happens twice in Tijuana (PDT, then PST).
    expect(localDateTimeToInstant('2026-11-01', '01:30', TIJUANA)).toBe(
      '2026-11-01T08:30:00.000Z',
    );
  });

  it('handles 23- and 25-hour days', () => {
    const spring = localDayBounds('2026-03-08', TIJUANA);
    expect(spring).toEqual({
      start: '2026-03-08T08:00:00.000Z',
      end: '2026-03-09T07:00:00.000Z',
    });
    expect(hoursBetween(spring.start, spring.end)).toBe(23);

    const fall = localDayBounds('2026-11-01', TIJUANA);
    expect(fall).toEqual({
      start: '2026-11-01T07:00:00.000Z',
      end: '2026-11-02T08:00:00.000Z',
    });
    expect(hoursBetween(fall.start, fall.end)).toBe(25);
  });

  it('gives 24-hour days in Mexico City (no DST since 2022)', () => {
    const day = localDayBounds('2026-10-17', MX);
    expect(day).toEqual({
      start: '2026-10-17T06:00:00.000Z',
      end: '2026-10-18T06:00:00.000Z',
    });
  });
});

describe('strict parsing', () => {
  it.each([
    '2026-10-17T23:30:00',
    '2026-10-17',
    '2026-02-30T10:00:00Z',
    '17/10/2026',
    '2026-10-17 23:30:00Z',
    '',
  ])('rejects the instant %j', (value) => {
    expect(() => parseInstant(value)).toThrow(RangeError);
    expect(() => toLocalDate(value, MX)).toThrow(/Invalid instant/);
  });

  it.each([
    '2026-02-30',
    '17/10/2026',
    '2026-1-5',
    '2026-13-01',
    '',
    '2026-10-17T00:00',
  ])('rejects the calendar date %j', (value) => {
    expect(() => parseLocalDate(value)).toThrow(RangeError);
    expect(() => formatLocalDate(value, { language: 'es' })).toThrow(
      /Invalid calendar date/,
    );
    expect(() => localDayBounds(value, MX)).toThrow(RangeError);
  });

  it.each(['24:00', '9:30', '23:60', '23:30:00', ''])(
    'rejects the local time %j',
    (value) => {
      expect(() => localDateTimeToInstant('2026-10-17', value, MX)).toThrow(
        /Invalid local time/,
      );
    },
  );

  it('rejects an unknown time zone', () => {
    const instant = '2026-10-18T05:30:00Z';
    expect(() => toLocalDate(instant, 'Mars/Base')).toThrow(
      /Unknown time zone: "Mars\/Base"/,
    );
    expect(() =>
      formatInstant(instant, { timeZone: 'Mars/Base', language: 'es' }),
    ).toThrow(RangeError);
    expect(() => todayInZone('Mars/Base')).toThrow(RangeError);
    expect(() =>
      localDateTimeToInstant('2026-10-17', '10:00', 'Mars/Base'),
    ).toThrow(RangeError);
  });
});

describe('calendar helpers (#57)', () => {
  it('adds months, clamping the day to the month', () => {
    expect(addMonths('2026-01-31', 1)).toBe('2026-02-28');
    expect(addMonths('2028-01-31', 1)).toBe('2028-02-29');
    expect(addMonths('2026-11-15', 2)).toBe('2027-01-15');
    expect(addMonths('2026-01-15', -1)).toBe('2025-12-15');
  });

  it('formats the zone offset at an instant', () => {
    expect(formatUtcOffset('2026-11-01T08:30:00Z', 'America/Tijuana')).toBe(
      'UTC−07:00',
    );
    expect(formatUtcOffset('2026-11-01T09:30:00Z', 'America/Tijuana')).toBe(
      'UTC−08:00',
    );
    expect(formatUtcOffset('2026-11-01T09:30:00Z', 'Asia/Kolkata')).toBe(
      'UTC+05:30',
    );
  });

  it('tells the DST transition days', () => {
    expect(hasOffsetChange('2026-11-01', 'America/Tijuana')).toBe(true);
    expect(hasOffsetChange('2026-03-08', 'America/Tijuana')).toBe(true);
    expect(hasOffsetChange('2026-11-02', 'America/Tijuana')).toBe(false);
    expect(hasOffsetChange('2026-11-01', 'America/Mexico_City')).toBe(false);
  });
});
