import { expect, type APIRequest, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';
import { SIGNED_OUT_STATE } from './auth';

// Families and guardian accounts created through the real API (#172 J1)
// with the page's own session (the shared demo Owner state): no login. The
// page must be on the app origin first. Names carry the caller's unique
// suffix.

async function csrf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

export async function createFamilyViaApi(
  page: Page,
  name: string,
): Promise<string> {
  const created = await api(page, '/api/v1/families', {
    method: 'POST',
    json: { name },
    headers: { 'X-CSRF-Token': await csrf(page) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/** Adds a student or a guardian profile to a family (PUT, idempotent). */
export async function addToFamilyViaApi(
  page: Page,
  familyId: string,
  kind: 'students' | 'guardians',
  memberId: string,
): Promise<void> {
  const added = await api(
    page,
    `/api/v1/families/${familyId}/${kind}/${memberId}`,
    {
      method: 'PUT',
      headers: { 'X-CSRF-Token': await csrf(page) },
    },
  );
  expect(added.status).toBe(204);
}

/**
 * An active guardian account with `password`, without a login: the shared
 * Owner session invites it (the guardian role needs no reauthentication),
 * then a signed-out API context redeems the invitation. Budget: 0 logins and
 * 1 redemption call (`token_redemption.ip`, see support/auth.ts). Returns
 * the new account's id.
 */
export async function createGuardianAccountViaPage(
  page: Page,
  request: APIRequest,
  baseURL: string,
  {
    username,
    password,
    displayName,
  }: { username: string; password: string; displayName: string },
): Promise<string> {
  const invited = await api(page, '/api/v1/invitations', {
    method: 'POST',
    json: { username, display_name: displayName, role: 'guardian' },
    headers: { 'X-CSRF-Token': await csrf(page) },
  });
  expect(invited.status).toBe(201);
  const { user_id: userId, invitation_url: url } = invited.body as {
    user_id: string;
    invitation_url: string;
  };
  const token = new URLSearchParams(new URL(url).hash.slice(1)).get('token');
  expect(token).toBeTruthy();
  // SIGNED_OUT_STATE: a new API context would otherwise inherit the shared
  // Owner session (see support/users.ts).
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
  return userId;
}
