import {
  useLayoutEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import {
  applyTheme,
  readStoredChoice,
  resolveTheme,
  subscribeToSystemTheme,
  systemPrefersDark,
  writeStoredChoice,
  type ThemeChoice,
} from './theme';
import { ThemeContext, type ThemeContextValue } from './use-theme';

// Holds the light/dark/system choice and keeps <html> in sync with it.
// Switching only changes a class on <html>, so the page is never reloaded
// and children keep their state. In `system` mode an OS change applies live.
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(readStoredChoice);
  const prefersDark = useSyncExternalStore(
    subscribeToSystemTheme,
    systemPrefersDark,
  );
  const resolvedTheme = resolveTheme(choice, prefersDark);

  useLayoutEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      choice,
      resolvedTheme,
      setChoice: (next: ThemeChoice) => {
        writeStoredChoice(next);
        setChoiceState(next);
      },
    }),
    [choice, resolvedTheme],
  );

  return <ThemeContext value={value}>{children}</ThemeContext>;
}
