import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { LANGUAGES, type Language } from './language';

interface LanguageSwitcherProps {
  value: Language;
  onValueChange: (language: Language) => void;
}

// Built like ThemeSwitcher: a labelled group of toggle buttons with
// aria-pressed. Each option is the language's own name (endonym) and carries
// its own lang attribute (WCAG 2.2 SC 3.1.2).
export function LanguageSwitcher({
  value,
  onValueChange,
}: LanguageSwitcherProps) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t('language.label')}
      className="inline-flex gap-1"
    >
      {LANGUAGES.map((language) => {
        const selected = language === value;
        return (
          <Button
            key={language}
            type="button"
            size="sm"
            lang={language}
            variant={selected ? 'default' : 'outline'}
            aria-pressed={selected}
            onClick={() => onValueChange(language)}
          >
            {t(`language.options.${language}`)}
          </Button>
        );
      })}
    </div>
  );
}
