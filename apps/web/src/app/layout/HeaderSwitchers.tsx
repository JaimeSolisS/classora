import { LanguageSwitcher } from '@/i18n/LanguageSwitcher';
import { useLanguage } from '@/i18n/use-language';
import { ThemeSwitcher } from '@/theme/ThemeSwitcher';
import { useTheme } from '@/theme/use-theme';

// The #14 theme switcher and the #15 language switcher, unchanged, wired to
// their stored choices.
export function HeaderThemeSwitcher() {
  const { choice, setChoice } = useTheme();
  return <ThemeSwitcher value={choice} onValueChange={setChoice} />;
}

export function HeaderLanguageSwitcher() {
  const { language, setLanguage } = useLanguage();
  return <LanguageSwitcher value={language} onValueChange={setLanguage} />;
}
