// Money display (ADR-0004). Amounts arrive from the API as decimal strings
// (NUMERIC(14,2), MXN) and are formatted without ever becoming a float:
// Intl.NumberFormat formats a decimal string exactly. The format is es-MX in
// both UI languages, so amounts read "$1,250.00", never "MX$1,250.00".
// Rates (NUMERIC(14,4)) are out of scope here; payroll adds them.

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
