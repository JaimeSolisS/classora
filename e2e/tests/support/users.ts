import { expect, type APIRequest } from '@playwright/test';
import { SIGNED_OUT_STATE } from './auth';
import { demoOwner } from './credentials';

// Test users created through the real API (#30 M1). Budget: one login
// (`login.ip`) and two redemption calls (`token_redemption.ip`) per user; see
// LOGIN_ATTEMPTS_PER_RUN and TOKEN_REDEMPTIONS_PER_RUN in support/auth.ts.

export type ActiveUserRole = 'tutor' | 'guardian';

export interface ActiveUserInput {
  username: string;
  /** Tutor or guardian: invitations for them need no reauthentication. */
  role: ActiveUserRole;
  password: string;
  displayName?: string;
}

/**
 * Creates an active user with `password`: in a fresh API context (its own
 * cookies; the shared browser state is never touched) the demo Owner signs
 * in and invites the user, then the invitation is looked up and redeemed.
 * Returns nothing secret beyond what the test passed in.
 */
export async function createActiveUser(
  request: APIRequest,
  baseURL: string,
  { username, role, password, displayName = username }: ActiveUserInput,
): Promise<void> {
  // The app's Origin, as the browser would send it (#24).
  const origin = new URL(baseURL).origin;
  // SIGNED_OUT_STATE: under the test runner a new API context inherits the
  // config's storageState (the shared Owner session); a login sent with that
  // cookie would revoke the shared session (#23 rotation).
  const context = await request.newContext({
    baseURL,
    storageState: SIGNED_OUT_STATE,
    extraHTTPHeaders: { Origin: origin },
  });
  try {
    const login = await context.post('/api/v1/auth/login', {
      data: { username: demoOwner.username, password: demoOwner.password },
    });
    expect(login.status()).toBe(200);
    const { csrf_token: csrf } = (await login.json()) as { csrf_token: string };

    const invited = await context.post('/api/v1/invitations', {
      data: { username, display_name: displayName, role },
      headers: { 'X-CSRF-Token': csrf },
    });
    expect(invited.status()).toBe(201);
    const { invitation_url: url } = (await invited.json()) as {
      invitation_url: string;
    };
    const token = new URLSearchParams(new URL(url).hash.slice(1)).get('token');
    expect(token).toBeTruthy();

    const lookup = await context.post('/api/v1/auth/invitation/lookup', {
      data: { token },
    });
    expect(lookup.status()).toBe(200);
    const redeem = await context.post('/api/v1/auth/invitation/redeem', {
      data: { token, password },
    });
    expect(redeem.status()).toBe(204);
  } finally {
    await context.dispose();
  }
}
