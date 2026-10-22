// Time and date helpers (spec §16, ADR-0004). Rules:
// - Instants (sessions, timestamps) are ISO 8601 strings WITH an offset or Z.
//   They are shown in the center's time zone, never the browser's.
// - Calendar dates (DATE columns) are `LocalDate` strings (YYYY-MM-DD). They
//   are never converted between time zones, so every viewer sees the same day.
// - Local wall-clock times are `HH:mm` strings.
// - Every helper takes the time zone as a required argument. Nothing here
//   depends on the browser's (host's) time zone.
// This is the only file allowed to use getUTC* and toISOString() slicing
// (ESLint enforces it). Here the UTC fields of a Date hold wall-clock values
// computed for an explicit zone (see WallClock), never "the UTC date".
//
// How it works: date-fns-tz gives the zone's UTC offset at an instant (IANA
// database through Intl, historic offsets included); the wall-clock fields
// are instant + offset; date-fns formats those fields with the language's
// locale. date-fns-tz's own formatInTimeZone/toZonedTime are not used for
// output because they rebuild the wall time in the host zone, which shifts a
// time that falls into a host DST gap.
import { format, type Locale } from 'date-fns';
import { formatInTimeZone } from 'date-fns-tz';
import { enUS } from 'date-fns/locale/en-US';
import { es } from 'date-fns/locale/es';
import type { Language } from '@/i18n/language';

/**
 * The default center time zone (ADR-0004). The center's zone is a business
 * setting (#37) that the app reads from the session through
 * `useCenterTimeZone()`; this value is only for signed-out pages and tests.
 */
export const DEFAULT_CENTER_TIME_ZONE = 'America/Mexico_City';

declare const localDateBrand: unique symbol;
/** A calendar date, `YYYY-MM-DD`, with no time or zone attached. */
export type LocalDate = string & { readonly [localDateBrand]: true };
/** A wall-clock time, `HH:mm` (24-hour). */
export type LocalTime = string;
/** An ISO 8601 instant with an offset or `Z`. */
export type Instant = string;

export type InstantStyle = 'date' | 'time' | 'dateTime';
export type LocalDateStyle = 'date' | 'long';

/** date-fns locale for each UI language (the only mapping). */
const DATE_LOCALES: Record<Language, Locale> = { es, en: enUS };

// Named styles (date-fns format tokens). Spanish uses 24-hour time.
//   es: date "17 oct 2026", time "23:30", dateTime "17 oct 2026, 23:30",
//       long "17 de octubre de 2026"
//   en: date "Oct 17, 2026", time "11:30 PM",
//       dateTime "Oct 17, 2026, 11:30 PM", long "October 17, 2026"
const PATTERNS: Record<
  Language,
  Record<InstantStyle, string> & { long: string }
> = {
  es: {
    date: 'd MMM yyyy',
    time: 'HH:mm',
    dateTime: 'd MMM yyyy, HH:mm',
    long: "d 'de' MMMM 'de' yyyy",
  },
  en: {
    date: 'MMM d, yyyy',
    time: 'h:mm a',
    dateTime: 'MMM d, yyyy, h:mm a',
    long: 'MMMM d, yyyy',
  },
};

const LOCAL_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const LOCAL_TIME = /^([01]\d|2[0-3]):([0-5]\d)$/;
const INSTANT =
  /^(\d{4}-\d{2}-\d{2})T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d{1,9})?)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/;

const DAY_MS = 86_400_000;

/**
 * A Date whose local getters return its UTC fields. date-fns keeps the Date
 * subclass it is given, so `format` prints the wall-clock fields stored in
 * the UTC fields and never consults the host zone.
 */
class WallClock extends Date {
  override getFullYear() {
    return this.getUTCFullYear();
  }
  override getMonth() {
    return this.getUTCMonth();
  }
  override getDate() {
    return this.getUTCDate();
  }
  override getDay() {
    return this.getUTCDay();
  }
  override getHours() {
    return this.getUTCHours();
  }
  override getMinutes() {
    return this.getUTCMinutes();
  }
  override getSeconds() {
    return this.getUTCSeconds();
  }
  override getMilliseconds() {
    return this.getUTCMilliseconds();
  }
  override getTimezoneOffset() {
    return 0;
  }
}

// ---------------------------------------------------------------- parsing

/** Throws a RangeError unless `timeZone` is a known IANA zone. */
export function assertTimeZone(timeZone: string): void {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone });
  } catch {
    throw new RangeError(`Unknown time zone: "${timeZone}"`);
  }
}

function isRealDate(value: string): boolean {
  const match = LOCAL_DATE.exec(value);
  if (!match) {
    return false;
  }
  const [year, month, day] = match.slice(1).map(Number) as [
    number,
    number,
    number,
  ];
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    year >= 1 &&
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

/** Validates a `YYYY-MM-DD` calendar date (real day, strict shape). */
export function parseLocalDate(value: string): LocalDate {
  if (isRealDate(value)) {
    return value as LocalDate;
  }
  throw new RangeError(
    `Invalid calendar date: "${value}" (expected a real date as YYYY-MM-DD)`,
  );
}

function parseLocalTime(value: string): { hours: number; minutes: number } {
  const match = LOCAL_TIME.exec(value);
  if (!match) {
    throw new RangeError(`Invalid local time: "${value}" (expected HH:mm)`);
  }
  return { hours: Number(match[1]), minutes: Number(match[2]) };
}

/** Parses an ISO 8601 instant; an offset or `Z` is required. */
export function parseInstant(value: string): Date {
  const match = INSTANT.exec(value);
  if (match && LOCAL_DATE.test(match[1]!) && isRealDate(match[1]!)) {
    const ms = Date.parse(value);
    if (!Number.isNaN(ms)) {
      return new Date(ms);
    }
  }
  throw new RangeError(
    `Invalid instant: "${value}" (expected ISO 8601 with an offset or Z)`,
  );
}

// ---------------------------------------------------------------- instants

export interface FormatInstantOptions {
  timeZone: string;
  language: Language;
  style?: InstantStyle;
}

const OFFSET = /^([+-])(\d{2}):(\d{2})$/;

/** UTC offset of `timeZone` at an instant, in ms (UTC−6 → −21 600 000). */
function offsetAt(timeZone: string, epochMs: number): number {
  // The `xxx` token is computed from the instant itself, not the host zone.
  const text = formatInTimeZone(new Date(epochMs), timeZone, 'xxx');
  const match = OFFSET.exec(text);
  if (!match) {
    throw new RangeError(`Unknown time zone: "${timeZone}"`);
  }
  const sign = match[1] === '-' ? -1 : 1;
  return sign * (Number(match[2]) * 60 + Number(match[3])) * 60_000;
}

/** The wall-clock fields of an instant in `timeZone`. */
function wallClock(date: Date, timeZone: string): WallClock {
  assertTimeZone(timeZone);
  const epoch = date.getTime();
  return new WallClock(epoch + offsetAt(timeZone, epoch));
}

/** Formats an instant as seen in `timeZone` (default style `dateTime`). */
export function formatInstant(
  instant: Instant,
  { timeZone, language, style = 'dateTime' }: FormatInstantOptions,
): string {
  return format(
    wallClock(parseInstant(instant), timeZone),
    PATTERNS[language][style],
    { locale: DATE_LOCALES[language] },
  );
}

function localDateOf(date: Date, timeZone: string): LocalDate {
  return format(wallClock(date, timeZone), 'yyyy-MM-dd') as LocalDate;
}

/**
 * The calendar date of `instant` in `timeZone`. Never derived from the UTC
 * date: 2026-10-18T05:30:00Z is 2026-10-17 in America/Mexico_City.
 */
export function toLocalDate(instant: Instant, timeZone: string): LocalDate {
  return localDateOf(parseInstant(instant), timeZone);
}

/** The wall-clock time (HH:MM, 24-hour) of `instant` in `timeZone`. */
export function toLocalTime(instant: Instant, timeZone: string): LocalTime {
  return format(wallClock(parseInstant(instant), timeZone), 'HH:mm');
}

/** Today's calendar date in `timeZone`; `now` is injectable for tests. */
export function todayInZone(
  timeZone: string,
  now: Date = new Date(),
): LocalDate {
  return localDateOf(now, timeZone);
}

// ---------------------------------------------------------- calendar dates

/** The calendar fields of a LocalDate, held as a WallClock (no zone). */
function calendarDay(localDate: string): WallClock {
  const [year, month, day] = parseLocalDate(localDate)
    .split('-')
    .map(Number) as [number, number, number];
  return new WallClock(Date.UTC(year, month - 1, day));
}

/**
 * Formats a calendar date as the same day for every viewer and host zone.
 * It never goes through `new Date('YYYY-MM-DD')` or any zone conversion:
 * the year, month and day are formatted as they are.
 */
export function formatLocalDate(
  localDate: LocalDate | string,
  { language, style = 'date' }: { language: Language; style?: LocalDateStyle },
): string {
  return format(calendarDay(localDate), PATTERNS[language][style], {
    locale: DATE_LOCALES[language],
  });
}

/** The ISO weekday of a calendar date: 1 = Monday … 7 = Sunday (no zone). */
export function isoWeekday(localDate: LocalDate | string): number {
  const day = calendarDay(localDate).getUTCDay();
  return day === 0 ? 7 : day;
}

/** Calendar arithmetic on a LocalDate (no zone involved). */
export function addDays(
  localDate: LocalDate | string,
  days: number,
): LocalDate {
  const next = new Date(calendarDay(localDate).getTime() + days * DAY_MS);
  return next.toISOString().slice(0, 10) as LocalDate;
}

// ------------------------------------------------------- local → instant

/**
 * The instant (ISO string with `Z`) of a wall-clock time in `timeZone`,
 * using the IANA database (historic offsets included).
 *
 * Daylight-saving edge cases follow Temporal's `disambiguation: 'compatible'`:
 * - a time inside a spring-forward gap moves forward by the gap length
 *   (America/Tijuana 2026-03-08 02:30 → 03:30 PDT);
 * - an ambiguous fall-back time takes the earlier instant
 *   (America/Tijuana 2026-11-01 01:30 → 01:30 PDT = 08:30Z).
 * date-fns-tz's fromZonedTime moves gap times backward (and depends on the
 * host zone), so the candidates are resolved here from the zone's offsets.
 */
export function localDateTimeToInstant(
  localDate: LocalDate | string,
  localTime: LocalTime,
  timeZone: string,
): Instant {
  assertTimeZone(timeZone);
  const [year, month, day] = parseLocalDate(localDate)
    .split('-')
    .map(Number) as [number, number, number];
  const { hours, minutes } = parseLocalTime(localTime);
  // The wall-clock fields read as if they were UTC.
  const wall = Date.UTC(year, month - 1, day, hours, minutes);

  // Offsets in force a day before and after cover any single transition.
  const before = offsetAt(timeZone, wall - DAY_MS);
  const after = offsetAt(timeZone, wall + DAY_MS);
  const candidates = [...new Set([before, after])]
    .map((offset) => wall - offset)
    .filter((epoch) => epoch + offsetAt(timeZone, epoch) === wall)
    .sort((a, b) => a - b);

  // One match: normal time. Two: ambiguous, take the earlier. None: gap,
  // apply the offset from before the transition (moves the time forward).
  const epoch = candidates[0] ?? wall - before;
  return new Date(epoch).toISOString();
}

/**
 * Start and end instants of a local day; `end` is exclusive (the next day's
 * start), so 23- and 25-hour days come out right.
 */
export function localDayBounds(
  localDate: LocalDate | string,
  timeZone: string,
): { start: Instant; end: Instant } {
  return {
    start: localDateTimeToInstant(localDate, '00:00', timeZone),
    end: localDateTimeToInstant(addDays(localDate, 1), '00:00', timeZone),
  };
}

// ------------------------------------------------------ calendar helpers

/**
 * Calendar arithmetic on a LocalDate by whole months (no zone involved). The
 * day is clamped to the target month's last day (2026-01-31 + 1 → 2026-02-28).
 */
export function addMonths(
  localDate: LocalDate | string,
  months: number,
): LocalDate {
  const [year, month, day] = parseLocalDate(localDate)
    .split('-')
    .map(Number) as [number, number, number];
  const index = year * 12 + (month - 1) + months;
  const targetYear = Math.floor(index / 12);
  const targetMonth = index - targetYear * 12;
  const lastDay = new Date(
    Date.UTC(targetYear, targetMonth + 1, 0),
  ).getUTCDate();
  const target = new Date(
    Date.UTC(targetYear, targetMonth, Math.min(day, lastDay)),
  );
  return target.toISOString().slice(0, 10) as LocalDate;
}

/** The UTC offset of `timeZone` at an instant, as text ("UTC−07:00"). */
export function formatUtcOffset(instant: Instant, timeZone: string): string {
  assertTimeZone(timeZone);
  const text = formatInTimeZone(parseInstant(instant), timeZone, 'xxx');
  return `UTC${text.replace('-', '−')}`;
}

/**
 * Whether the zone's UTC offset changes during the local day (a DST
 * transition day: its length is not 24 hours).
 */
export function hasOffsetChange(
  localDate: LocalDate | string,
  timeZone: string,
): boolean {
  const { start, end } = localDayBounds(localDate, timeZone);
  return Date.parse(end) - Date.parse(start) !== DAY_MS;
}
