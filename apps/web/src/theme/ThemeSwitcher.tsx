import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { THEME_CHOICES, type ThemeChoice } from './theme';

interface ThemeSwitcherProps {
  value: ThemeChoice;
  onValueChange: (choice: ThemeChoice) => void;
}

// A labelled group of toggle buttons. Each button is reachable with Tab and
// activated with Enter or Space; aria-pressed exposes the current choice.
export function ThemeSwitcher({ value, onValueChange }: ThemeSwitcherProps) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t('theme.label')}
      className="inline-flex gap-1"
    >
      {THEME_CHOICES.map((choice) => {
        const selected = choice === value;
        return (
          <Button
            key={choice}
            type="button"
            size="sm"
            variant={selected ? 'default' : 'outline'}
            aria-pressed={selected}
            onClick={() => onValueChange(choice)}
          >
            {t(`theme.options.${choice}`)}
          </Button>
        );
      })}
    </div>
  );
}
