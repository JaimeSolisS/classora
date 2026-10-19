import { readFileSync } from 'node:fs';

// UI text read from the web app's own translation files, so tests never
// repeat a string. The configured locale is es-MX → the `es` catalogs; the
// English test reads `en`.

type Common = {
  apiStatus: { live: string };
  notFound: { title: string };
  nav: {
    home: string;
    users: string;
    invitations: string;
    passwordResets: string;
    registrations: string;
  };
  roles: Record<string, string>;
  delegations: Record<string, { label: string; description: string }>;
  auth: {
    login: {
      title: string;
      username: string;
      password: string;
      submit: string;
      invitationRedeemed: string;
      passwordReset: string;
      requestAccount: string;
    };
    reauth: { title: string; password: string; confirm: string };
    userMenu: { signedInAs: string; signOut: string };
  };
  invitations: {
    title: string;
    form: {
      username: string;
      displayName: string;
      role: string;
      submit: string;
    };
    result: { link: string };
  };
  oneTimeLink: { onceWarning: string };
  passwordResets: {
    title: string;
    form: { username: string; submit: string; self: string };
    result: { link: string };
  };
  reset: { title: string };
  account: {
    password: {
      title: string;
      link: string;
      current: string;
      new: string;
      confirm: string;
      submit: string;
      done: string;
    };
  };
  register: {
    title: string;
    role: { legend: string; guardian: string; student: string };
    displayName: string;
    username: string;
    password: string;
    confirmPassword: string;
    submit: string;
    sent: string;
    backToLogin: string;
  };
  users: {
    title: string;
    list: { search: string };
    status: {
      suspend: string;
      archive: string;
      reactivate: string;
      suspendTitle: string;
      reactivateTitle: string;
    };
    roles: { title: string; save: string; saved: string };
    delegations: { title: string; save: string; saved: string };
  };
  userStates: Record<'pending' | 'active' | 'suspended' | 'archived', string>;
  registrations: {
    title: string;
    approve: string;
    approveFor: string;
    approved: string;
  };
  invite: {
    title: string;
    username: string;
    newPassword: string;
    confirmPassword: string;
    submit: string;
    incomplete: string;
  };
};

type Errors = {
  invalid_credentials: string;
  token_invalid: string;
  current_password_invalid: string;
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
