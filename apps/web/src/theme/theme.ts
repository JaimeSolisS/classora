// Theme choice, storage and application to <html>. Kept free of React so
// main.tsx can apply the stored theme before the first render.
// The value under STORAGE_KEY is this device's choice: signed out it is the
// only one. At sign-in the account's stored theme wins and replaces it; an
// account that never stored one is filled from it (#36,
// src/features/preferences). Signed in, a switcher choice is stored here and
// saved to the account.

export type ThemeChoice = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_CHOICES: readonly ThemeChoice[] = [
  'light',
  'dark',
  'system',
];
export const STORAGE_KEY = 'classora.theme';
export const DEFAULT_CHOICE: ThemeChoice = 'system';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function isThemeChoice(value: unknown): value is ThemeChoice {
  return THEME_CHOICES.includes(value as ThemeChoice);
}

/** Stored choice, or `system` when nothing valid is stored or storage throws. */
export function readStoredChoice(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isThemeChoice(value) ? value : DEFAULT_CHOICE;
  } catch {
    return DEFAULT_CHOICE;
  }
}

/** Saves the choice; blocked storage only means it is not kept. */
export function writeStoredChoice(choice: ThemeChoice): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    // Storage is unavailable (for example blocked by the browser).
  }
}

function darkQuery(): MediaQueryList | null {
  return typeof window.matchMedia === 'function'
    ? window.matchMedia(DARK_QUERY)
    : null;
}

export function systemPrefersDark(): boolean {
  return darkQuery()?.matches ?? false;
}

/** Calls `onChange` whenever the OS color scheme changes; returns cleanup. */
export function subscribeToSystemTheme(onChange: () => void): () => void {
  const query = darkQuery();
  if (!query) {
    return () => {};
  }
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

export function resolveTheme(
  choice: ThemeChoice,
  prefersDark: boolean,
): ResolvedTheme {
  if (choice === 'system') {
    return prefersDark ? 'dark' : 'light';
  }
  return choice;
}

/** Applies a theme: the `dark` class on <html> plus a matching color-scheme. */
export function applyTheme(theme: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
}
