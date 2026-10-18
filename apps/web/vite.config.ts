import { storybookTest } from '@storybook/addon-vitest/vitest-plugin';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { playwright } from '@vitest/browser-playwright';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

// Story tests: every story (with its play function and the axe check) runs
// headlessly in Chromium, once per theme through the `theme` toolbar global.
// They run only through `make test-storybook`, never through `make test`.
function storybookProject(theme: 'light' | 'dark') {
  return {
    extends: true,
    plugins: [
      storybookTest({ configDir: '.storybook', initialGlobals: { theme } }),
    ],
    test: {
      name: `storybook-${theme}`,
      browser: {
        enabled: true,
        headless: true,
        provider: playwright(),
        instances: [{ browser: 'chromium' as const }],
      },
    },
  };
}

// UTC+14: the local date differs from the UTC date for most of the day.
const DEFAULT_TEST_TIME_ZONE = 'Pacific/Kiritimati';

// API_PROXY_TARGET is read here only (it is not VITE_-prefixed, so it never
// reaches client code). The SPA calls relative URLs and the dev server
// forwards them, so the browser sees one origin and the API needs no CORS.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const apiTarget = env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000';

  return {
    plugins: [react(), tailwindcss()],
    // The `@/` alias comes from `paths` in tsconfig.app.json, so Vite, Vitest,
    // Storybook and tsc all read it from one place.
    resolve: { tsconfigPaths: true },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: {
        '/health': apiTarget,
        '/api': apiTarget,
      },
    },
    test: {
      projects: [
        {
          extends: true,
          test: {
            name: 'unit',
            environment: 'jsdom',
            include: ['src/**/*.test.{ts,tsx}'],
            // Unit tests run under a host time zone far from UTC by default,
            // so a helper that leaks the host zone fails here and in CI.
            // An explicit TZ (e.g. `TZ=UTC make test-web`) takes precedence.
            env: { TZ: env.TZ || DEFAULT_TEST_TIME_ZONE },
            // Vitest replaces CSS with an empty module by default; the token
            // contrast test reads tokens.css as raw text.
            css: { include: [/tokens\.css/] },
          },
        },
        storybookProject('light'),
        storybookProject('dark'),
      ],
    },
  };
});
