// Scores and maximum scores (#308 F1, ADR-0004): decimals with at most two
// places, read from what the user types and compared as whole hundredths.
// Never `Number()` or `parseFloat` on a score or a limit: "10.1" is 1010
// hundredths exactly. The API takes and returns them as decimal strings
// ("7.50"), so values are sent with `formatHundredths`. Pure.

/** Whole hundredths: 7.5 is 750. */
export type Hundredths = number & { readonly __hundredths: unique symbol };

/**
 * Why a typed value isn't a score:
 * - `invalid`: not a number such as 7.5 (a sign, a letter, a leading or
 *   trailing ".", more than 4 integer digits);
 * - `comma`: a decimal comma ("5,5"): write "5.5";
 * - `precision`: more than two decimal places.
 */
export type DecimalError = 'invalid' | 'comma' | 'precision';

export type ParsedScore =
  { ok: true; hundredths: Hundredths } | { ok: false; error: DecimalError };

const SCORE = /^(\d{1,4})(?:\.(\d{1,2}))?$/;
const TOO_PRECISE = /^\d{1,4}\.\d{3,}$/;
const COMMA = /^\d{1,4},\d+$/;

/** The integer a run of at most six ASCII digits spells, digit by digit. */
function digitsValue(digits: string): number {
  let value = 0;
  for (const digit of digits) {
    value = value * 10 + (digit.charCodeAt(0) - 48);
  }
  return value;
}

/** Reads a typed score (surrounding spaces ignored). */
export function parseScore(text: string): ParsedScore {
  const value = text.trim();
  const match = SCORE.exec(value);
  if (match) {
    const whole = match[1]!;
    const cents = (match[2] ?? '').padEnd(2, '0');
    return {
      ok: true,
      hundredths: (digitsValue(whole) * 100 + digitsValue(cents)) as Hundredths,
    };
  }
  if (TOO_PRECISE.test(value)) {
    return { ok: false, error: 'precision' };
  }
  if (COMMA.test(value)) {
    return { ok: false, error: 'comma' };
  }
  return { ok: false, error: 'invalid' };
}

/** Reads a decimal string the API sent ("10.00"); null if it isn't one. */
export function hundredthsOf(text: string): Hundredths | null {
  const parsed = parseScore(text);
  return parsed.ok ? parsed.hundredths : null;
}

/** Negative, zero or positive, as `a` is below, equal to or above `b`. */
export function compareHundredths(a: Hundredths, b: Hundredths): number {
  return a - b;
}

/** The decimal string the API takes: 750 → "7.50". */
export function formatHundredths(value: Hundredths): string {
  const whole = Math.trunc(value / 100);
  const cents = value - whole * 100;
  return `${whole}.${String(cents).padStart(2, '0')}`;
}

/** The bounds of a template criterion's maximum score: 0.5 to 1000. */
export const MAX_SCORE_MIN = 50 as Hundredths;
export const MAX_SCORE_MAX = 100_000 as Hundredths;

export type ScoreRangeError = DecimalError | 'range';

/** A template's maximum score: a decimal from 0.5 to 1000. */
export function maxScoreError(text: string): ScoreRangeError | undefined {
  const parsed = parseScore(text);
  if (!parsed.ok) {
    return parsed.error;
  }
  return compareHundredths(parsed.hundredths, MAX_SCORE_MIN) < 0 ||
    compareHundredths(parsed.hundredths, MAX_SCORE_MAX) > 0
    ? 'range'
    : undefined;
}

/** A criterion's score: a decimal from 0 to `max` (the API's string). */
export function scoreError(
  text: string,
  max: string,
): ScoreRangeError | undefined {
  const parsed = parseScore(text);
  if (!parsed.ok) {
    return parsed.error;
  }
  const limit = hundredthsOf(max);
  return limit !== null && compareHundredths(parsed.hundredths, limit) > 0
    ? 'range'
    : undefined;
}

/**
 * A percentage or an average the API sent as decimal text ("66.67"), for
 * display exactly as sent (#322 B5, #308 E6): a passthrough, never
 * `Number()` or `parseFloat`, so nothing is rounded or recomputed on the
 * client. `null` (no data) stays `null`; the caller shows "No data yet".
 */
export function decimalText(value: string | null): string | null {
  return value;
}
