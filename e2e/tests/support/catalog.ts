import { readFileSync } from 'node:fs';

// UI text in the configured locale (es-MX → the web app's `es` catalog), read
// from the web app's own translation files so tests never repeat a string.
const catalogUrl = new URL(
  '../../../apps/web/src/i18n/locales/es/common.json',
  import.meta.url,
);

type Catalog = {
  apiStatus: { live: string };
  notFound: { title: string };
};

export const common = JSON.parse(readFileSync(catalogUrl, 'utf8')) as Catalog;
