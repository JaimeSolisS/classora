import { storybookTest } from '@storybook/addon-vitest/vitest-plugin';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { playwright } from '@vitest/browser-playwright';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import {
  DEVELOPMENT_HEADERS,
  PRODUCTION_HEADERS,
} from './src/security-headers';

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
  // The app's dev server (`make dev`, `make e2e`) enforces the development
  // CSP (src/security-headers.ts). Storybook (it sets STORYBOOK) and Vitest
  // (VITEST) reuse this file but serve their own inline scripts, so they get
  // no policy. loadEnv with the '' prefix also returns process variables.
  const appDevServer = !env.STORYBOOK && !env.VITEST;

  return {
    plugins: [react(), tailwindcss()],
    // The `@/` alias comes from `paths` in tsconfig.app.json, so Vite, Vitest,
    // Storybook and tsc all read it from one place.
    resolve: { tsconfigPaths: true },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      // Enforced (not Report-Only), also on the proxied API responses.
      headers: appDevServer ? { ...DEVELOPMENT_HEADERS } : undefined,
      proxy: {
        '/health': apiTarget,
        '/api': apiTarget,
      },
    },
    // `vite preview` serves a production build under the production policy
    // (the manual check of #24; M9-09 moves it into Caddy).
    preview: { headers: { ...PRODUCTION_HEADERS } },
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
