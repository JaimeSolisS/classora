// UI language choice and storage. Kept free of React and i18next so main.tsx
// can resolve the language before the first render.
// The value under LANGUAGE_STORAGE_KEY is this device's choice: signed out
// it is the only one. At sign-in the account's stored language wins and
// replaces it; an account that never stored one is filled from the language
// the app shows (#36, src/features/preferences). Signed in, a switcher choice
// is stored here and saved to the account.

export const LANGUAGES = ['es', 'en'] as const;
export type Language = (typeof LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = 'classora.language';
export const DEFAULT_LANGUAGE: Language = 'es';

export function isLanguage(value: unknown): value is Language {
  return LANGUAGES.includes(value as Language);
}

/** Stored language, or null when nothing valid is stored or storage throws. */
export function readStoredLanguage(): Language | null {
  try {
    const value = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return isLanguage(value) ? value : null;
  } catch {
    return null;
  }
}

/** Saves the language; blocked storage only means it is not kept. */
export function writeStoredLanguage(language: Language): void {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Storage is unavailable (for example blocked by the browser).
  }
}

/**
 * First browser language whose primary subtag is a supported language
 * (`es-MX` → `es`, `en-GB` → `en`), or null.
 */
export function languageFromList(
  languages: readonly string[] | null | undefined,
): Language | null {
  for (const tag of languages ?? []) {
    const primary =
      typeof tag === 'string' ? tag.split('-')[0]?.toLowerCase() : undefined;
    if (isLanguage(primary)) {
      return primary;
    }
  }
  return null;
}

function browserLanguages(): readonly string[] | null {
  try {
    return window.navigator.languages ?? null;
  } catch {
    return null;
  }
}

/** Stored choice, then the browser languages, then Spanish. */
export function resolveInitialLanguage(): Language {
  return (
    readStoredLanguage() ??
    languageFromList(browserLanguages()) ??
    DEFAULT_LANGUAGE
  );
}

/** Narrows an i18next language value to a supported language. */
export function toLanguage(value: string | undefined): Language {
  return isLanguage(value) ? value : DEFAULT_LANGUAGE;
}
