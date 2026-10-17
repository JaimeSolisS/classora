import { Button } from '@/components/ui/button';
import type { ThemeChoice } from './theme';

// English-only labels are an interim exception until M1-15 externalizes UI
// text (M1-14 E3).
const OPTIONS: { value: ThemeChoice; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
];

interface ThemeSwitcherProps {
  value: ThemeChoice;
  onValueChange: (choice: ThemeChoice) => void;
}

// A labelled group of toggle buttons. Each button is reachable with Tab and
// activated with Enter or Space; aria-pressed exposes the current choice.
export function ThemeSwitcher({ value, onValueChange }: ThemeSwitcherProps) {
  return (
    <div role="group" aria-label="Theme" className="inline-flex gap-1">
      {OPTIONS.map((option) => {
        const selected = option.value === value;
        return (
          <Button
            key={option.value}
            type="button"
            size="sm"
            variant={selected ? 'default' : 'outline'}
            aria-pressed={selected}
            onClick={() => onValueChange(option.value)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}
