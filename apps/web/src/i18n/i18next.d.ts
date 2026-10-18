import 'i18next';
import type common from './locales/es/common.json';
import type errors from './locales/es/errors.json';

// Types t() from the Spanish catalogs (the source language), so an unknown
// key is a tsc error in `make lint-web`. The missing-key check
// (check.test.ts) keeps the English catalogs in step.
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'common';
    resources: {
      common: typeof common;
      errors: typeof errors;
    };
  }
}
