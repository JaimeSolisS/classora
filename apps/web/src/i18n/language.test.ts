import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  LANGUAGE_STORAGE_KEY,
  languageFromList,
  readStoredLanguage,
  resolveInitialLanguage,
  writeStoredLanguage,
} from './language';

function stubLanguages(languages: readonly string[] | undefined) {
  vi.spyOn(window.navigator, 'languages', 'get').mockReturnValue(
    languages as readonly string[],
  );
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('resolveInitialLanguage', () => {
  it('uses a valid stored value first', () => {
    stubLanguages(['es-MX']);
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en');
    expect(resolveInitialLanguage()).toBe('en');
  });

  it('falls back to the first supported browser language', () => {
    stubLanguages(['fr-FR', 'en-GB', 'es-MX']);
    expect(resolveInitialLanguage()).toBe('en');
    stubLanguages(['es-MX', 'en-US']);
    expect(resolveInitialLanguage()).toBe('es');
  });

  it.each(['fr', 'ES', '', 'english'])(
    'ignores the unknown stored value %j',
    (stored) => {
      stubLanguages(['en-US']);
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, stored);
      expect(resolveInitialLanguage()).toBe('en');
    },
  );

  it('falls back to Spanish with no supported browser language', () => {
    stubLanguages(['fr-FR', 'de']);
    expect(resolveInitialLanguage()).toBe('es');
  });

  it.each([[[]], [undefined]])(
    'falls back to Spanish when navigator.languages is %j',
    (languages) => {
      stubLanguages(languages);
      expect(resolveInitialLanguage()).toBe('es');
    },
  );

  it('falls back when localStorage throws on read', () => {
    stubLanguages(['en-US']);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    expect(readStoredLanguage()).toBeNull();
    expect(resolveInitialLanguage()).toBe('en');
  });

  it('falls back when navigator.languages throws', () => {
    vi.spyOn(window.navigator, 'languages', 'get').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(resolveInitialLanguage()).toBe('es');
  });
});

describe('writeStoredLanguage', () => {
  it('stores the language under classora.language', () => {
    writeStoredLanguage('en');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
  });

  it('does not throw when localStorage throws on write', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    expect(() => writeStoredLanguage('en')).not.toThrow();
  });
});

describe('languageFromList', () => {
  it('matches on the primary subtag only', () => {
    expect(languageFromList(['es-MX'])).toBe('es');
    expect(languageFromList(['en-GB'])).toBe('en');
    expect(languageFromList(['EN-us'])).toBe('en');
    expect(languageFromList(['eus', 'fr'])).toBeNull();
  });
});
