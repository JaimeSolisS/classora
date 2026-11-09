import { describe, expect, it } from 'vitest';
import { matchesSearch, normalizeSearchText, searchTerms } from './search';

// #545 A6, D1: the combobox's local match ignores case and accents and
// takes every term in any order (the API's `every_term` rule).

describe('normalizeSearchText', () => {
  it('lower-cases and strips accents', () => {
    expect(normalizeSearchText('José')).toBe('jose');
    expect(normalizeSearchText('Ñúñez')).toBe('nunez');
    expect(normalizeSearchText('ÁLVARO Müller')).toBe('alvaro muller');
  });
});

describe('searchTerms', () => {
  it('splits on whitespace and keeps at most 5 terms', () => {
    expect(searchTerms('  Ana   María ')).toEqual(['ana', 'maria']);
    expect(searchTerms('a b c d e f g')).toEqual(['a', 'b', 'c', 'd', 'e']);
    expect(searchTerms('   ')).toEqual([]);
  });
});

describe('matchesSearch', () => {
  it('ignores accents and case', () => {
    expect(matchesSearch('José Pérez', 'jose')).toBe(true);
    expect(matchesSearch('jose perez', 'José')).toBe(true);
    expect(matchesSearch('Ana Ñúñez', 'nunez')).toBe(true);
    expect(matchesSearch('Ana Ñúñez', 'NUÑEZ')).toBe(true);
  });

  it('takes every term in any order, each as a substring', () => {
    expect(matchesSearch('José Pérez', 'perez jose')).toBe(true);
    expect(matchesSearch('José Pérez', 'pe jo')).toBe(true);
    expect(matchesSearch('José Pérez', 'jose luis')).toBe(false);
  });

  it('matches everything for a blank query', () => {
    expect(matchesSearch('José Pérez', '')).toBe(true);
    expect(matchesSearch('José Pérez', '   ')).toBe(true);
  });
});
