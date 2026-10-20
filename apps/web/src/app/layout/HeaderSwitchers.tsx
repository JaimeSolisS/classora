import { usePreferences } from '@/features/preferences/usePreferences';
import { LanguageSwitcher } from '@/i18n/LanguageSwitcher';
import { useLanguage } from '@/i18n/use-language';
import { ThemeSwitcher } from '@/theme/ThemeSwitcher';
import { useTheme } from '@/theme/use-theme';

// The #14 theme switcher and the #15 language switcher. A choice applies at
// once and is stored on this device; signed in, it is also saved to the
// account (#36 F4). Signed out, it stays local (F6).
export function HeaderThemeSwitcher() {
  const { choice } = useTheme();
  const { changeTheme } = usePreferences();
  return <ThemeSwitcher value={choice} onValueChange={changeTheme} />;
}

export function HeaderLanguageSwitcher() {
  const { language } = useLanguage();
  const { changeLanguage } = usePreferences();
  return <LanguageSwitcher value={language} onValueChange={changeLanguage} />;
}
