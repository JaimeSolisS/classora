import js from '@eslint/js';
import prettier from 'eslint-config-prettier';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import { defineConfig, globalIgnores } from 'eslint/config';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const ZOD_MESSAGE = "Import `z` from '@/lib/forms' (CSP-safe settings).";
const ZOD_PATH = { name: 'zod', message: ZOD_MESSAGE };
const ZOD_PATTERN = { group: ['zod/*'], message: ZOD_MESSAGE };
const RECHARTS_MESSAGE =
  'Recharts is imported only under src/features/progress/charts/ (lazy route chunks, #322 A3).';
const RECHARTS_PATH = { name: 'recharts', message: RECHARTS_MESSAGE };
const RECHARTS_PATTERN = { group: ['recharts/*'], message: RECHARTS_MESSAGE };

export default defineConfig([
  globalIgnores(['dist', 'coverage', 'storybook-static']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommendedTypeChecked,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  // Time guard (spec §16, ADR-0004): local dates come from src/lib/time.ts,
  // never from the UTC date of a Date. Only time.ts and tests may use these.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/lib/time.ts', 'src/**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-properties': [
        'error',
        ...['getUTCDate', 'getUTCMonth', 'getUTCFullYear'].map((property) => ({
          property,
          message:
            'Do not derive calendar dates from UTC: use toLocalDate/todayInZone from src/lib/time.ts.',
        })),
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "CallExpression > MemberExpression.callee[property.name=/^(slice|substring|substr)$/][object.type='CallExpression'][object.callee.property.name='toISOString']",
          message:
            'Do not slice toISOString() to get a date or time: use src/lib/time.ts.',
        },
      ],
    },
  },
  // Forms (#28): Zod is imported only through src/lib/forms.ts, which turns
  // off its eval probe that the Content Security Policy would report.
  // Charts (#322 A3): Recharts is imported only under
  // src/features/progress/charts/, which only the two lazy progress pages
  // use, so it stays out of the entry chunk. (One rule per file set: a later
  // no-restricted-imports entry replaces an earlier one.)
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/lib/forms.ts', 'src/features/progress/charts/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [ZOD_PATH, RECHARTS_PATH],
          patterns: [ZOD_PATTERN, RECHARTS_PATTERN],
        },
      ],
    },
  },
  {
    files: ['src/features/progress/charts/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        { paths: [ZOD_PATH], patterns: [ZOD_PATTERN] },
      ],
    },
  },
  {
    files: ['**/*.js'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
  // Last: turns off every rule that would conflict with Prettier formatting.
  prettier,
]);
