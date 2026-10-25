import { describe, expect, it } from 'vitest';
import {
  compareHundredths,
  formatHundredths,
  hundredthsOf,
  maxScoreError,
  parseScore,
  scoreError,
  type Hundredths,
} from './decimal';

// #308 F1: scores as whole hundredths, never a float.

const h = (value: number) => value as Hundredths;

describe('parseScore', () => {
  it.each([
    ['0', 0],
    ['0.5', 50],
    ['10.25', 1025],
    ['1000', 100_000],
    ['1000.01', 100_001],
    [' 7.5 ', 750],
    ['9.1', 910],
    ['0.07', 7],
  ])('reads %s as %d hundredths', (text, hundredths) => {
    expect(parseScore(text)).toEqual({ ok: true, hundredths });
  });

  it.each([
    ['10.255', 'precision'],
    ['5,5', 'comma'],
    ['-1', 'invalid'],
    ['abc', 'invalid'],
    ['', 'invalid'],
    ['.5', 'invalid'],
    ['5.', 'invalid'],
    ['12345', 'invalid'],
    ['1e2', 'invalid'],
    ['+3', 'invalid'],
  ])('refuses %j (%s)', (text, error) => {
    expect(parseScore(text)).toEqual({ ok: false, error });
  });

  it('is exact where a float is not (0.1 + 0.2)', () => {
    const a = parseScore('0.1');
    const b = parseScore('0.2');
    const c = parseScore('0.3');
    expect(a.ok && b.ok && c.ok).toBe(true);
    if (a.ok && b.ok && c.ok) {
      expect(a.hundredths + b.hundredths).toBe(c.hundredths);
    }
  });
});

describe('comparisons and formatting', () => {
  it('compares hundredths', () => {
    expect(compareHundredths(h(950), h(1000))).toBeLessThan(0);
    expect(compareHundredths(h(1000), h(1000))).toBe(0);
    expect(compareHundredths(h(1001), h(1000))).toBeGreaterThan(0);
  });

  it.each([
    [0, '0.00'],
    [50, '0.50'],
    [750, '7.50'],
    [1025, '10.25'],
    [100_000, '1000.00'],
  ])('formats %d as %s', (value, text) => {
    expect(formatHundredths(h(value))).toBe(text);
  });

  it('reads the decimal strings the API sends', () => {
    expect(hundredthsOf('10.00')).toBe(1000);
    expect(hundredthsOf('nope')).toBeNull();
  });
});

describe('maxScoreError (a template criterion: 0.5 to 1000)', () => {
  it.each([
    ['0.5', undefined],
    ['1000', undefined],
    ['1000.00', undefined],
    ['10', undefined],
    ['0.49', 'range'],
    ['0', 'range'],
    ['1000.01', 'range'],
    ['10.255', 'precision'],
    ['5,5', 'comma'],
    ['-1', 'invalid'],
    ['abc', 'invalid'],
  ])('%s → %s', (text, error) => {
    expect(maxScoreError(text)).toBe(error);
  });
});

describe('scoreError (0 to the criterion maximum)', () => {
  it.each([
    ['0', '10.00', undefined],
    ['10', '10.00', undefined],
    ['9.99', '10.00', undefined],
    ['10.01', '10.00', 'range'],
    ['10.1', '10.00', 'range'],
    ['10.255', '10.00', 'precision'],
    ['-1', '10.00', 'invalid'],
    ['5,5', '10.00', 'comma'],
    ['0.5', '0.50', undefined],
  ])('%s of %s → %s', (text, max, error) => {
    expect(scoreError(text, max)).toBe(error);
  });
});
