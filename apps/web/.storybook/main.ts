import type { StorybookConfig } from '@storybook/react-vite';

// Storybook reuses vite.config.ts (Tailwind, React and the `@/` alias).
const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  addons: ['@storybook/addon-a11y', '@storybook/addon-vitest'],
  framework: '@storybook/react-vite',
  core: { disableTelemetry: true },
};

export default config;
