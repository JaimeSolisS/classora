import type { Decorator, Preview } from '@storybook/react-vite';
import { applyLanguage, i18n } from '@/i18n';
import { isLanguage } from '@/i18n/language';
import { applyTheme } from '@/theme/theme';
import '@/styles/index.css';

// Applies the toolbar theme with the same `dark` class and tokens the app
// uses (src/theme/theme.ts), so every story can be checked in both themes.
const withTheme: Decorator = (Story, context) => {
  applyTheme(context.globals.theme === 'dark' ? 'dark' : 'light');
  return <Story />;
};

// Applies the toolbar language through the app's i18n instance, which also
// sets <html lang>. Generic component stories keep English sample copy
// (fixture data); stories of app components render through the catalogs.
const withLocale: Decorator = (Story, context) => {
  const locale: unknown = context.globals.locale;
  const language = isLanguage(locale) ? locale : 'es';
  if (i18n.language !== language) {
    applyLanguage(language);
  }
  return <Story />;
};

const preview: Preview = {
  globalTypes: {
    theme: {
      description: 'Color theme',
      toolbar: {
        title: 'Theme',
        icon: 'mirror',
        items: [
          { value: 'light', title: 'Light' },
          { value: 'dark', title: 'Dark' },
        ],
        dynamicTitle: true,
      },
    },
    locale: {
      description: 'UI language',
      toolbar: {
        title: 'Language',
        icon: 'globe',
        items: [
          { value: 'es', title: 'Español' },
          { value: 'en', title: 'English' },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: { theme: 'light', locale: 'es' },
  decorators: [withTheme, withLocale],
  parameters: {
    // Axe violations fail the story test (`make test-storybook`), which runs
    // every story once per theme. The rules: WCAG 2.0/2.1/2.2 A and AA (the
    // same tags as the E2E scans, _docs/testing-guidelines.md) plus axe's
    // best practices. Listing the tags also turns on rules axe leaves off by
    // default, such as target-size (WCAG 2.2, SC 2.5.8).
    a11y: {
      test: 'error',
      options: {
        runOnly: {
          type: 'tag',
          values: [
            'wcag2a',
            'wcag2aa',
            'wcag21a',
            'wcag21aa',
            'wcag22aa',
            'best-practice',
          ],
        },
      },
    },
  },
};

export default preview;
