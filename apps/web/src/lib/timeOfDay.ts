// Times of day as a form holds them (HF-13, #598): `HH:MM`, 24-hour, with
// no date and no zone. Reading what a person types, showing a time in the
// app language, and the quarter-hour options of a time field. Pure: no
// React, no Intl state.

/** A time of day, `HH:MM` (00:00–23:59). */
export const TIME_OF_DAY = /^([01]\d|2[0-3]):[0-5]\d$/;

const DAY_MINUTES = 24 * 60;

/** Minutes since midnight of a valid `HH:MM`, else null. */
export function minutesOfDay(time: string): number | null {
  if (!TIME_OF_DAY.test(time)) {
    return null;
  }
  const [hours, minutes] = time.split(':').map(Number) as [number, number];
  return hours * 60 + minutes;
}

/** `HH:MM` of minutes since midnight (0–1439), else null. */
export function timeOfMinutes(total: number): string | null {
  if (!Number.isInteger(total) || total < 0 || total >= DAY_MINUTES) {
    return null;
  }
  const hours = String(Math.floor(total / 60)).padStart(2, '0');
  const minutes = String(total % 60).padStart(2, '0');
  return `${hours}:${minutes}`;
}

// The hour and the minutes, with an optional `:` or `.` between them
// (`9`, `9:`, `9:3`, `9:30`, `09.30`), or digits only (`930`, `0930`).
const SEPARATED = /^(\d{1,2})(?:[:.](\d{0,2}))?$/;
const DIGITS = /^(\d{1,2})(\d{2})$/;
// a, am, a.m., a. m., p, pm, p.m. (any case): the same in es and en.
const MERIDIEM = /^(.*?)\s*([ap])\.?\s*(?:m\.?)?$/i;

/**
 * The `HH:MM` a person means by `text`, or null when it can't be read.
 * Read as 24-hour unless it ends in am/pm. Any whole minute.
 */
export function parseTime(text: string): string | null {
  // Any space (an Intl narrow no-break space too) counts as one space.
  let rest = text.trim().replace(/\s+/g, ' ');
  if (rest === '') {
    return null;
  }
  let meridiem: 'a' | 'p' | null = null;
  const marked = MERIDIEM.exec(rest);
  if (marked && /\d$/.test(marked[1] ?? '')) {
    rest = marked[1] ?? '';
    meridiem = (marked[2] ?? '').toLowerCase() as 'a' | 'p';
  }
  const parts = SEPARATED.exec(rest) ?? DIGITS.exec(rest);
  if (!parts) {
    return null;
  }
  let hours = Number(parts[1]);
  const minutes = parts[2] ? Number(parts[2]) : 0;
  if (minutes > 59) {
    return null;
  }
  if (meridiem) {
    if (hours < 1 || hours > 12) {
      return null;
    }
    hours = (hours % 12) + (meridiem === 'p' ? 12 : 0);
  }
  return hours > 23 ? null : timeOfMinutes(hours * 60 + minutes);
}

/**
 * A time in the app language: es 24-hour `09:30`, en 12-hour `9:30 AM`
 * (the calendar's hour labels). Anything but `HH:MM` is returned as is.
 */
export function formatTime(time: string, language: 'es' | 'en'): string {
  const total = minutesOfDay(time);
  if (total === null || language === 'es') {
    return time;
  }
  const hours = Math.floor(total / 60);
  const minutes = String(total % 60).padStart(2, '0');
  const twelve = hours % 12 === 0 ? 12 : hours % 12;
  return `${twelve}:${minutes} ${hours < 12 ? 'AM' : 'PM'}`;
}

/** Every `stepMinutes` from 00:00 to the last one before 24:00. */
export function timeOptions(stepMinutes = 15): string[] {
  const options: string[] = [];
  for (let total = 0; total < DAY_MINUTES; total += stepMinutes) {
    options.push(timeOfMinutes(total) ?? '');
  }
  return options;
}

/** `time` plus `minutes`, or null when it leaves the day. */
export function addMinutes(time: string, minutes: number): string | null {
  const total = minutesOfDay(time);
  return total === null ? null : timeOfMinutes(total + minutes);
}

/** Letters and digits only, lower case: what the list filter compares. */
function compact(text: string): string {
  return text.toLowerCase().replace(/[^0-9a-z]/g, '');
}

/**
 * Whether an option matches typed text: its label, `HH:MM` or `H:MM`
 * starts with it, ignoring spaces, dots, colons and case (`930` and
 * `9:30 p` both match 9:30 PM).
 */
export function timeMatches(
  option: string,
  text: string,
  language: 'es' | 'en',
): boolean {
  const wanted = compact(text);
  if (wanted === '') {
    return true;
  }
  const unpadded = option.replace(/^0(\d)/, '$1');
  return [formatTime(option, language), option, unpadded].some((candidate) =>
    compact(candidate).startsWith(wanted),
  );
}
