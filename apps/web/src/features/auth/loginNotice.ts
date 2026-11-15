import type { ParseKeys } from 'i18next';

// The notice the login page shows once after a public page sent the person
// there (#29 J5): router state, never the URL, so the username never goes
// into `next` or a query string and a reload drops it: after an invitation
// (#29) or a password reset (#30), or after the person changed their own
// roles and was signed out (#170 G4).

/** The kinds of login notice and their `common` catalog key. */
export const LOGIN_NOTICE_KEYS = {
  invitation_redeemed: 'auth.login.invitationRedeemed',
  password_reset: 'auth.login.passwordReset',
  roles_changed: 'auth.login.rolesChanged',
} as const satisfies Record<string, ParseKeys<'common'>>;

export type LoginNoticeKind = keyof typeof LOGIN_NOTICE_KEYS;

export interface LoginNoticeState {
  notice: LoginNoticeKind;
  /** Pre-fills the username field. */
  username: string;
}

/** The router state to navigate to /login with. */
export function loginNoticeState(
  notice: LoginNoticeKind,
  username: string,
): LoginNoticeState {
  return { notice, username };
}

/** The notice in a router state, or null for anything else. */
export function readLoginNotice(state: unknown): LoginNoticeState | null {
  if (typeof state !== 'object' || state === null) {
    return null;
  }
  const { notice, username } = state as Record<string, unknown>;
  if (
    typeof notice !== 'string' ||
    !Object.hasOwn(LOGIN_NOTICE_KEYS, notice) ||
    typeof username !== 'string' ||
    username.length === 0 ||
    username.length > 256
  ) {
    return null;
  }
  return { notice: notice as LoginNoticeKind, username };
}
