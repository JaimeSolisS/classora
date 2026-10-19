import { readFileSync } from 'node:fs';

// UI text read from the web app's own translation files, so tests never
// repeat a string. The configured locale is es-MX → the `es` catalogs; the
// English test reads `en`.

type Common = {
  apiStatus: { live: string };
  notFound: { title: string };
  nav: { home: string };
  auth: {
    login: {
      title: string;
      username: string;
      password: string;
      submit: string;
    };
    userMenu: { signedInAs: string; signOut: string };
  };
};

type Errors = {
  invalid_credentials: string;
};

function read<T>(path: string): T {
  return JSON.parse(
    readFileSync(
      new URL(`../../../apps/web/src/i18n/locales/${path}`, import.meta.url),
      'utf8',
    ),
  ) as T;
}

export const common = read<Common>('es/common.json');
export const commonEn = read<Common>('en/common.json');
export const errors = read<Errors>('es/errors.json');

/** `Signed in as {{name}}` with the name filled in. */
export function signedInAs(catalog: Common, name: string): string {
  return catalog.auth.userMenu.signedInAs.replace('{{name}}', name);
}
