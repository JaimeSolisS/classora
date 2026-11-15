// Money display (ADR-0004). Amounts arrive from the API as decimal strings
// (NUMERIC(14,2), MXN) and are formatted without ever becoming a float:
// Intl.NumberFormat formats a decimal string exactly. The format is es-MX in
// both UI languages, so amounts read "$1,250.00", never "MX$1,250.00".
// Rates (NUMERIC(14,4), M7) have their own format and input check below.

const MONEY_FORMAT = new Intl.NumberFormat('es-MX', {
  style: 'currency',
  currency: 'MXN',
});

// NUMERIC(14,2): up to 12 integer digits and up to 2 decimals.
const MONEY = /^-?\d{1,12}(\.\d{1,2})?$/;
const NEGATIVE_ZERO = /^-0+(\.0+)?$/;

type DecimalString = `${number}`;

function isMoneyString(amount: string): amount is DecimalString {
  return MONEY.test(amount);
}

/** Formats an MXN amount given as a decimal string, e.g. "1250.00". */
export function formatMoney(amount: string): string {
  if (typeof amount !== 'string' || !isMoneyString(amount)) {
    throw new RangeError(
      `Invalid money amount: "${String(amount)}" (expected a decimal string with up to 12 integer digits and 2 decimals)`,
    );
  }
  // "-0.00" is zero: show "$0.00", not "-$0.00".
  return MONEY_FORMAT.format(NEGATIVE_ZERO.test(amount) ? '0' : amount);
}

// Money typed into a form (#363 C2): a non-negative amount with up to 12
// integer digits and up to 2 decimals, e.g. "250" or "350.50". It stays a
// string all the way to the API body, never a number. The server stays
// authoritative (money_invalid, money_precision, money_out_of_range,
// amount_negative).
const MONEY_INPUT = /^\d{1,12}(\.\d{1,2})?$/;

/** The typed amount as sent: trimmed, otherwise as typed ("250" stays "250"). */
export function normalizeMoneyInput(value: string): string {
  return value.trim();
}

/** Whether a typed amount is a valid non-negative money amount. */
export function isMoneyInput(value: string): boolean {
  return MONEY_INPUT.test(normalizeMoneyInput(value));
}

// A pay rate (NUMERIC(14,4), #425 C6, G2): up to 10 integer digits and up to
// 4 decimals. Shown with the API's precision, trimmed to at least two places
// ("250.1250" is "$250.125", "250.0000" is "$250.00"), never through a float.
const RATE_FORMAT = new Intl.NumberFormat('es-MX', {
  style: 'currency',
  currency: 'MXN',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const RATE = /^\d{1,10}(\.\d{1,4})?$/;

function isRateString(rate: string): rate is DecimalString {
  return RATE.test(rate);
}

/** Formats a pay rate given as a decimal string, e.g. "250.1250". */
export function formatRate(rate: string): string {
  if (typeof rate !== 'string' || !isRateString(rate)) {
    throw new RangeError(
      `Invalid rate: "${String(rate)}" (expected a non-negative decimal string with up to 10 integer digits and 4 decimals)`,
    );
  }
  return RATE_FORMAT.format(rate);
}

/** Whether a typed rate is valid: not negative, up to four decimals. */
export function isRateInput(value: string): boolean {
  return RATE.test(normalizeMoneyInput(value));
}
