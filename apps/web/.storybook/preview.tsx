import type { Decorator, Preview } from '@storybook/react-vite';
import { applyTheme } from '@/theme/theme';
import '@/styles/index.css';

// Applies the toolbar theme with the same `dark` class and tokens the app
// uses (src/theme/theme.ts), so every story can be checked in both themes.
const withTheme: Decorator = (Story, context) => {
  applyTheme(context.globals.theme === 'dark' ? 'dark' : 'light');
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
  },
  initialGlobals: { theme: 'light' },
  decorators: [withTheme],
  parameters: {
    // Axe violations fail the story test (`make test-storybook`), which runs
    // every story once per theme.
    a11y: { test: 'error' },
  },
};

export default preview;
