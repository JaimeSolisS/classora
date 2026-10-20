import { createContext, useContext } from 'react';
import { useLanguage } from '@/i18n/use-language';
import type { Language } from '@/i18n/language';
import { useTheme } from '@/theme/use-theme';
import type { ThemeChoice } from '@/theme/theme';
import type { SaveFailure } from './controller';

// What the header switchers use (#36 F4, F5): choose a language or theme
// (applied at once; saved to the account while signed in), the failed save
// to show, and "Try again". Filled by the auth provider.

export interface PreferencesContextValue {
  changeLanguage: (language: Language) => void;
  changeTheme: (theme: ThemeChoice) => void;
  /** The last failed save, or null. */
  failure: SaveFailure | null;
  /** Resends the latest value of the failed field(s). */
  retry: () => void;
}

export const PreferencesContext = createContext<PreferencesContextValue | null>(
  null,
);

/**
 * The preferences context. Without the auth provider (stories, components
 * rendered alone) the switchers work locally only, as in M1.
 */
export function usePreferences(): PreferencesContextValue {
  const value = useContext(PreferencesContext);
  const { setChoice } = useTheme();
  const { setLanguage } = useLanguage();
  return (
    value ?? {
      changeLanguage: setLanguage,
      changeTheme: setChoice,
      failure: null,
      retry: () => {},
    }
  );
}
