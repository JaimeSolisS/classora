import { describe, expect, it } from 'vitest';
import { chartValue } from './chartValue';

// #322 B5: the decimal text becomes a number for the y position only.
describe('chartValue', () => {
  it.each([
    ['66.67', 66.67],
    ['80.00', 80],
    ['0.00', 0],
    ['100.00', 100],
  ])('%s → %s', (text, value) => {
    expect(chartValue(text)).toBe(value);
  });

  it('null → null (a gap)', () => {
    expect(chartValue(null)).toBeNull();
  });

  it('text that is not a number → null', () => {
    expect(chartValue('n/a')).toBeNull();
  });
});
