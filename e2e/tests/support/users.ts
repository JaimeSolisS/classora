import {
  expect,
  type APIRequest,
  type APIRequestContext,
} from '@playwright/test';
import { OWNER_STATE_PATH, SIGNED_OUT_STATE } from './auth';
import { demoOwner } from './credentials';

// Test users created through the real API (#30 M1). Budget: one redemption
// call (`token_redemption.ip`) per user. A tutor or guardian is invited with
// the shared demo Owner session (no login, #36); a staff role needs a
// reauthentication, so the demo Owner signs in in its own API context (one
// login, `login.ip`) and reauthenticates there (#170 I2; it counts against
// `reauth.user` only when the password is wrong, never here). See
// LOGIN_ATTEMPTS_PER_RUN and TOKEN_REDEMPTIONS_PER_RUN in support/auth.ts.

export type ActiveUserRole =
  | 'tutor'
  | 'guardian'
  | 'administrator'
  | 'academic_coordinator'
  | 'finance_staff';

/** Inviting for these roles needs a recent reauthentication (#29). */
const STAFF_ROLES: ReadonlySet<ActiveUserRole> = new Set([
  'administrator',
  'academic_coordinator',
  'finance_staff',
]);

export interface ActiveUserInput {
  username: string;
  /**
   * Tutor or guardian, or a staff role: the helper then reauthenticates the
   * demo Owner in its own API context before inviting (#170 I2).
   */
  role: ActiveUserRole;
  password: string;
  displayName?: string;
}

/**
 * The demo Owner's API context and its CSRF token: the shared session for a
 * tutor or guardian (no login; it never logs in or out, so the shared state
 * stays valid), or a fresh login for a staff role, which then
 * reauthenticates (the shared session is never reauthenticated).
 */
async function ownerContext(
  request: APIRequest,
  baseURL: string,
  staff: boolean,
): Promise<{ context: APIRequestContext; csrf: string }> {
  // The app's Origin, as the browser would send it (#24).
  const origin = new URL(baseURL).origin;
  if (!staff) {
    const context = await request.newContext({
      baseURL,
      storageState: OWNER_STATE_PATH,
      extraHTTPHeaders: { Origin: origin },
    });
    const me = await context.get('/api/v1/auth/me');
    expect(me.status()).toBe(200);
    const { csrf_token: csrf } = (await me.json()) as { csrf_token: string };
    return { context, csrf };
  }
  // SIGNED_OUT_STATE: under the test runner a new API context inherits the
  // config's storageState (the shared Owner session); a login sent with that
  // cookie would revoke the shared session (#23 rotation).
  const context = await request.newContext({
    baseURL,
    storageState: SIGNED_OUT_STATE,
    extraHTTPHeaders: { Origin: origin },
  });
  const login = await context.post('/api/v1/auth/login', {
    data: { username: demoOwner.username, password: demoOwner.password },
  });
  expect(login.status()).toBe(200);
  const { csrf_token: csrf } = (await login.json()) as { csrf_token: string };
  const reauth = await context.post('/api/v1/auth/reauthenticate', {
    data: { password: demoOwner.password },
    headers: { 'X-CSRF-Token': csrf },
  });
  expect(reauth.status()).toBe(200);
  return { context, csrf };
}

/**
 * Creates an active user with `password`: the demo Owner invites the user
 * (see `ownerContext`), then a signed-out API context redeems the
 * invitation. The shared browser state is never changed.
 * Returns nothing secret beyond what the test passed in.
 */
export async function createActiveUser(
  request: APIRequest,
  baseURL: string,
  { username, role, password, displayName = username }: ActiveUserInput,
): Promise<void> {
  const owner = await ownerContext(request, baseURL, STAFF_ROLES.has(role));
  let token: string | null;
  try {
    const invited = await owner.context.post('/api/v1/invitations', {
      data: { username, display_name: displayName, role },
      headers: { 'X-CSRF-Token': owner.csrf },
    });
    expect(invited.status()).toBe(201);
    const { invitation_url: url } = (await invited.json()) as {
      invitation_url: string;
    };
    token = new URLSearchParams(new URL(url).hash.slice(1)).get('token');
    expect(token).toBeTruthy();
  } finally {
    await owner.context.dispose();
  }

  // No lookup call: auth/invitations.spec.ts covers it, and each call
  // counts against `token_redemption.ip` (#34).
  const context = await request.newContext({
    baseURL,
    storageState: SIGNED_OUT_STATE,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
  });
  try {
    const redeem = await context.post('/api/v1/auth/invitation/redeem', {
      data: { token, password },
    });
    expect(redeem.status()).toBe(204);
  } finally {
    await context.dispose();
  }
}
