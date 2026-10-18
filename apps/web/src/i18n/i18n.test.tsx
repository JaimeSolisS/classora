import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { applyLanguage, i18n } from './index';
import { LANGUAGE_STORAGE_KEY } from './language';
import { LanguageSwitcher } from './LanguageSwitcher';
import { useLanguage } from './use-language';

(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function ConnectedSwitcher() {
  const { language, setLanguage } = useLanguage();
  return <LanguageSwitcher value={language} onValueChange={setLanguage} />;
}

function button(name: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(
    (b) => b.textContent === name,
  );
  if (!found) {
    throw new Error(`No button "${name}"`);
  }
  return found;
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  window.localStorage.clear();
});

describe('i18n instance', () => {
  it('is initialized synchronously with bundled catalogs', () => {
    expect(i18n.isInitialized).toBe(true);
    applyLanguage('es');
    expect(i18n.language).toBe('es');
    expect(i18n.t('apiStatus.live')).toBe('API: activa');
    expect(i18n.t('errors:generic')).toBe(
      'Algo salió mal. Inténtalo de nuevo.',
    );
    applyLanguage('en');
    expect(i18n.t('apiStatus.live')).toBe('API: live');
  });

  it('types keys from the catalogs', () => {
    // @ts-expect-error: unknown keys are a tsc error (B3).
    expect(i18n.t('common:doesNotExist')).toBe('doesNotExist');
  });

  it('keeps <html lang> in sync with the active language', () => {
    applyLanguage('en');
    expect(document.documentElement.lang).toBe('en');
    applyLanguage('es');
    expect(document.documentElement.lang).toBe('es');
  });
});

describe('LanguageSwitcher', () => {
  it.each([
    ['es', 'Idioma'],
    ['en', 'Language'],
  ] as const)(
    'renders a labelled group with endonyms in %s',
    (language, label) => {
      applyLanguage(language);
      act(() => root.render(<ConnectedSwitcher />));

      const group = container.querySelector('[role="group"]');
      expect(group?.getAttribute('aria-label')).toBe(label);
      expect(button('Español').getAttribute('lang')).toBe('es');
      expect(button('English').getAttribute('lang')).toBe('en');
      expect(button('Español').getAttribute('aria-pressed')).toBe(
        String(language === 'es'),
      );
      // No raw key is ever shown.
      expect(container.textContent).not.toMatch(/language\.|common:/);
    },
  );

  it('switches language, stores it and updates <html lang>', () => {
    applyLanguage('es');
    act(() => root.render(<ConnectedSwitcher />));

    act(() => button('English').click());

    expect(i18n.language).toBe('en');
    expect(document.documentElement.lang).toBe('en');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en');
    expect(button('English').getAttribute('aria-pressed')).toBe('true');
    expect(
      container.querySelector('[role="group"]')?.getAttribute('aria-label'),
    ).toBe('Language');

    act(() => button('Español').click());
    expect(document.documentElement.lang).toBe('es');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('es');
  });
});
