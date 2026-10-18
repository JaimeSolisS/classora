import { useTranslation } from 'react-i18next';
import { applyLanguage } from './index';
import { toLanguage, writeStoredLanguage, type Language } from './language';

export interface LanguageState {
  language: Language;
  setLanguage: (language: Language) => void;
}

// Active language plus a setter that stores the choice. Switching re-renders
// translated components in place: no reload, no remount.
export function useLanguage(): LanguageState {
  const { i18n } = useTranslation();
  return {
    language: toLanguage(i18n.resolvedLanguage),
    setLanguage: (language: Language) => {
      writeStoredLanguage(language);
      applyLanguage(language);
    },
  };
}
