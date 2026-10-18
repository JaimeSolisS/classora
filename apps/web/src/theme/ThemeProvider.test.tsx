import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { STORAGE_KEY, applyTheme, readStoredChoice } from './theme';
import { ThemeProvider } from './ThemeProvider';
import { ThemeSwitcher } from './ThemeSwitcher';
import { useTheme } from './use-theme';

(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

// A controllable stand-in for matchMedia('(prefers-color-scheme: dark)').
function mockSystemTheme(initiallyDark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const query = {
    matches: initiallyDark,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) =>
      listeners.add(cb),
    removeEventListener: (
      _type: string,
      cb: (e: MediaQueryListEvent) => void,
    ) => listeners.delete(cb),
  };
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => query),
  );
  return {
    setDark(dark: boolean) {
      query.matches = dark;
      for (const cb of listeners) {
        cb({ matches: dark } as MediaQueryListEvent);
      }
    },
  };
}

let mounts = 0;

// Counts mounts and keeps local state, to prove switching never remounts.
function StatefulChild() {
  const [clicks, setClicks] = useState(0);
  useEffect(() => {
    mounts += 1;
  }, []);
  return (
    <button type="button" onClick={() => setClicks((n) => n + 1)}>
      clicks:{clicks}
    </button>
  );
}

function ConnectedSwitcher() {
  const { choice, setChoice } = useTheme();
  return <ThemeSwitcher value={choice} onValueChange={setChoice} />;
}

let container: HTMLDivElement;
let root: Root;

function render(): void {
  act(() =>
    root.render(
      <ThemeProvider>
        <StatefulChild />
        <ConnectedSwitcher />
      </ThemeProvider>,
    ),
  );
}

function click(name: string): void {
  const button = [...container.querySelectorAll('button')].find(
    (b) => b.textContent === name,
  );
  if (!button) {
    throw new Error(`No button "${name}"`);
  }
  act(() => button.click());
}

function htmlIsDark(): boolean {
  return document.documentElement.classList.contains('dark');
}

function pressed(name: string): string | null {
  const button = [...container.querySelectorAll('button')].find(
    (b) => b.textContent === name,
  );
  return button?.getAttribute('aria-pressed') ?? null;
}

beforeEach(() => {
  // These tests read the English labels; one test below checks Spanish.
  applyLanguage('en');
  mounts = 0;
  localStorage.clear();
  applyTheme('light');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('ThemeProvider', () => {
  it('switches light → dark → system without remounting and stores each choice', () => {
    mockSystemTheme(false);
    render();
    click('clicks:0');

    click('Light');
    expect(htmlIsDark()).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
    expect(pressed('Light')).toBe('true');

    click('Dark');
    expect(htmlIsDark()).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
    expect(pressed('Dark')).toBe('true');
    expect(pressed('Light')).toBe('false');

    click('System');
    expect(htmlIsDark()).toBe(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('system');

    expect(mounts).toBe(1);
    expect(container.textContent).toContain('clicks:1');
  });

  it('follows a live OS color scheme change in system mode', () => {
    const system = mockSystemTheme(false);
    render();
    expect(pressed('System')).toBe('true');
    expect(htmlIsDark()).toBe(false);

    act(() => system.setDark(true));
    expect(htmlIsDark()).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe('dark');

    act(() => system.setDark(false));
    expect(htmlIsDark()).toBe(false);
  });

  it('ignores OS changes while an explicit theme is chosen', () => {
    const system = mockSystemTheme(false);
    render();
    click('Light');
    act(() => system.setDark(true));
    expect(htmlIsDark()).toBe(false);
  });

  it('applies the stored choice on load', () => {
    mockSystemTheme(false);
    localStorage.setItem(STORAGE_KEY, 'dark');
    render();
    expect(pressed('Dark')).toBe('true');
    expect(htmlIsDark()).toBe(true);
  });

  it('falls back to system when nothing is stored', () => {
    mockSystemTheme(true);
    expect(readStoredChoice()).toBe('system');
    render();
    expect(pressed('System')).toBe('true');
    expect(htmlIsDark()).toBe(true);
  });

  it('falls back to system for an unknown stored value', () => {
    mockSystemTheme(true);
    localStorage.setItem(STORAGE_KEY, 'blue');
    expect(readStoredChoice()).toBe('system');
    render();
    expect(pressed('System')).toBe('true');
    expect(htmlIsDark()).toBe(true);
  });

  it('falls back to system without an error when storage throws', () => {
    mockSystemTheme(false);
    const consoleError = vi.spyOn(console, 'error');
    const blocked = {
      getItem: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
      setItem: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
    };
    vi.stubGlobal('localStorage', blocked);

    expect(readStoredChoice()).toBe('system');
    render();
    expect(pressed('System')).toBe('true');

    click('Dark');
    expect(htmlIsDark()).toBe(true);
    expect(pressed('Dark')).toBe('true');
    expect(consoleError).not.toHaveBeenCalled();
  });

  it('works when matchMedia is unavailable', () => {
    vi.stubGlobal('matchMedia', undefined);
    render();
    expect(pressed('System')).toBe('true');
    expect(htmlIsDark()).toBe(false);
  });
});

describe('ThemeSwitcher labels', () => {
  it.each([
    ['es', 'Tema', ['Claro', 'Oscuro', 'Sistema']],
    ['en', 'Theme', ['Light', 'Dark', 'System']],
  ] as const)('come from the %s catalog', (language, label, options) => {
    applyLanguage(language);
    render();
    expect(
      container.querySelector('[role="group"]')?.getAttribute('aria-label'),
    ).toBe(label);
    const names = [...container.querySelectorAll('[role="group"] button')].map(
      (b) => b.textContent,
    );
    expect(names).toEqual(options);
  });
});
