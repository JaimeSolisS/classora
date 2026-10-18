import { describe, expect, it } from 'vitest';
import { formatMoney } from './money';

// Exact strings for Node 24's ICU (the CI runtime).
describe('formatMoney', () => {
  it.each([
    ['1250.00', '$1,250.00'],
    ['-1250.5', '-$1,250.50'],
    ['0', '$0.00'],
    ['999999999999.99', '$999,999,999,999.99'],
    ['-999999999999.99', '-$999,999,999,999.99'],
    ['0.1', '$0.10'],
    ['12', '$12.00'],
  ])('formats %j as %j', (amount, expected) => {
    expect(formatMoney(amount)).toBe(expected);
  });

  it.each(['-0.00', '-0', '-0.0', '-00.00'])(
    'formats negative zero %j as $0.00',
    (amount) => {
      expect(formatMoney(amount)).toBe('$0.00');
    },
  );

  it('formats every digit of a 14-digit amount exactly', () => {
    expect(formatMoney('123456789012.35')).toBe('$123,456,789,012.35');
  });

  it.each([
    '',
    'abc',
    '1,250.00',
    '1e3',
    ' 12.00',
    '12.00 ',
    '1250.001',
    '1234567890123.00',
    '+12.00',
    '.5',
    '12.',
    'NaN',
    'Infinity',
  ])('rejects %j with a RangeError, without rounding', (amount) => {
    expect(() => formatMoney(amount)).toThrow(RangeError);
  });

  it('rejects a number argument at compile time and at runtime', () => {
    // @ts-expect-error: amounts are decimal strings, never numbers.
    expect(() => formatMoney(1250)).toThrow(RangeError);
  });

  it('uses es-MX in both UI languages (no "MX$")', () => {
    expect(formatMoney('1250.00')).not.toContain('MX');
  });
});
