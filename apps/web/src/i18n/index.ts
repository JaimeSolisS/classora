import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { DEFAULT_LANGUAGE, LANGUAGES, type Language } from './language';
import enCommon from './locales/en/common.json';
import enErrors from './locales/en/errors.json';
import esCommon from './locales/es/common.json';
import esErrors from './locales/es/errors.json';

// One i18next instance for the whole app. The catalogs are bundled and the
// init is synchronous, so the first render already shows translated text
// (no Suspense fallback, no catalog request, no raw keys on screen).
// Namespaces: `common` (app text) and `errors` (error messages).
export const NAMESPACES = ['common', 'errors'] as const;
export const DEFAULT_NAMESPACE = 'common';

export const resources = {
  es: { common: esCommon, errors: esErrors },
  en: { common: enCommon, errors: enErrors },
} as const;

export const i18n = i18next.createInstance();

/**
 * Whether a missing key is logged (#130 C5): in a development build only
 * (the Vite dev server, which the E2E stack runs), never in production or
 * under Vitest, where many tests render fallbacks on purpose.
 */
export function reportsMissingKeys(env: {
  DEV: boolean;
  MODE: string;
}): boolean {
  return env.DEV && env.MODE !== 'test';
}

/** The development log line the E2E language spec fails on. */
export const MISSING_KEY_LOG = '[i18n] missing key';

const reportMissing = reportsMissingKeys(import.meta.env);

// <html lang> always follows the active language (WCAG 2.2 SC 3.1.1).
i18n.on('languageChanged', (language: string) => {
  document.documentElement.lang = language;
});

void i18n.use(initReactI18next).init({
  resources,
  lng: DEFAULT_LANGUAGE,
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: LANGUAGES,
  ns: NAMESPACES,
  defaultNS: DEFAULT_NAMESPACE,
  initAsync: false,
  // React escapes interpolated values. Translations are always rendered as
  // text, never as raw HTML (use <Trans> for rich text).
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
  saveMissing: reportMissing,
  missingKeyHandler: reportMissing
    ? (lngs, ns, key) => {
        console.error(MISSING_KEY_LOG, ns, key, lngs);
      }
    : false,
});

/**
 * Switches the UI language. The catalogs are bundled, so the switch (and the
 * <html lang> update) happens synchronously.
 */
export function applyLanguage(language: Language): void {
  void i18n.changeLanguage(language);
}
