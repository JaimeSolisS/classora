import { expect, type APIRequest, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';
import { createApprovedAccount } from './consent';
import { reauthenticatedOwnerContext } from './users';

// Tutors created through the real API (#43 F2) with the page's own session
// (the shared demo Owner state): an invitation only, so no login and no
// redemption call (`token_redemption.ip`). The invited user is `pending`
// and already holds the tutor role, so the tutor pages show them. The page
// must be on the app origin first.

export async function inviteTutorViaApi(
  page: Page,
  { username, displayName }: { username: string; displayName: string },
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const invited = await api(page, '/api/v1/invitations', {
    method: 'POST',
    json: { username, display_name: displayName, role: 'tutor' },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(invited.status).toBe(201);
  return (invited.body as { user_id: string }).user_id;
}

/**
 * An active tutor who can sign in, without any token redemption (#44; the
 * `token_redemption.ip` budget is full): a self-registration as a guardian
 * that the Owner's page session approves (`createApprovedAccount`, 1
 * registration), then the demo Owner, signed in in its own API context and
 * reauthenticated (1 login), replaces the account's roles with `tutor`.
 * The page must be on the app origin first. Returns the user id.
 */
export async function createApprovedTutor(
  page: Page,
  request: APIRequest,
  baseURL: string,
  {
    username,
    displayName,
    password,
  }: { username: string; displayName: string; password: string },
): Promise<string> {
  const userId = await createApprovedAccount(page, request, baseURL, {
    username,
    displayName,
    password,
    role: 'guardian',
  });
  const user = await api(page, `/api/v1/users/${userId}`);
  expect(user.status).toBe(200);
  const { version } = user.body as { version: number };
  const owner = await reauthenticatedOwnerContext(request, baseURL);
  try {
    const changed = await owner.context.put(`/api/v1/users/${userId}/roles`, {
      data: { roles: ['tutor'], version },
      headers: { 'X-CSRF-Token': owner.csrf },
    });
    expect(changed.status()).toBe(200);
  } finally {
    await owner.context.dispose();
  }
  return userId;
}

/**
 * A new active subject named `name` under the seeded education level
 * `levelName` (#32 seed), through the API with the page's session (the
 * shared demo Owner). The page must be on the app origin first. Returns the
 * subject id.
 */
export async function createSubjectViaApi(
  page: Page,
  levelName: string,
  name: string,
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const levels = await api(
    page,
    '/api/v1/education-levels?status=active&limit=100',
  );
  expect(levels.status).toBe(200);
  const level = (
    levels.body as { items: { id: string; name: string }[] }
  ).items.find((item) => item.name === levelName);
  expect(level).toBeTruthy();
  const created = await api(
    page,
    `/api/v1/education-levels/${level!.id}/subjects`,
    {
      method: 'POST',
      json: { name },
      headers: { 'X-CSRF-Token': csrfTokenOf(me) },
    },
  );
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}
