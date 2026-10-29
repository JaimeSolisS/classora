// Money as whole cents for the allocation arithmetic (#379 D2): the API's
// decimal strings ("1250.00", NUMERIC(14,2)) and typed amounts are read
// digit by digit, never through Number() or parseFloat, so "0.10" + "0.20"
// is exactly 30 cents. 12 integer digits and 2 decimals fit a safe integer.
// Pure.

/** Whole cents: $12.50 is 1250. */
export type Cents = number & { readonly __cents: unique symbol };

const AMOUNT = /^(-?)(\d{1,12})(?:\.(\d{1,2}))?$/;

/** The cents of a decimal string ("12.5" → 1250); null if it isn't one. */
export function centsOf(text: string): Cents | null {
  const match = AMOUNT.exec(text.trim());
  if (!match) {
    return null;
  }
  const whole = Number.parseInt(match[2]!, 10);
  const fraction = Number.parseInt((match[3] ?? '').padEnd(2, '0'), 10);
  const value = whole * 100 + fraction;
  return (match[1] === '-' ? -value : value) as Cents;
}

/** The cents of an API amount (always valid); 0 if it isn't one. */
export function apiCents(text: string): Cents {
  return centsOf(text) ?? (0 as Cents);
}

/** The decimal string the API takes and formatMoney reads: 1250 → "12.50". */
export function centsText(value: Cents): string {
  const sign = value < 0 ? '-' : '';
  const absolute = Math.abs(value);
  const whole = Math.trunc(absolute / 100);
  const fraction = absolute - whole * 100;
  return `${sign}${whole}.${String(fraction).padStart(2, '0')}`;
}

export function sumCents(values: readonly Cents[]): Cents {
  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total as Cents;
}

export function minCents(a: Cents, b: Cents): Cents {
  return a < b ? a : b;
}
