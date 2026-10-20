import { expect, type APIRequest, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';
import { SIGNED_OUT_STATE } from './auth';

// Student logins and accounts for the consent spec (#35 K1), through the
// real API with the page's own session (the shared demo Owner state): no
// login. The page must be on the app origin first.

async function csrf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

async function linkAccount(
  page: Page,
  studentId: string,
  userId: string,
): Promise<void> {
  const student = await api(page, `/api/v1/students/${studentId}`);
  expect(student.status).toBe(200);
  const { version } = student.body as { version: number };
  const linked = await api(page, `/api/v1/students/${studentId}/account`, {
    method: 'PUT',
    json: { user_id: userId, version },
    headers: { 'X-CSRF-Token': await csrf(page) },
  });
  expect(linked.status).toBe(200);
}

/**
 * K1: the demo Owner invites `username` with the Student role (no
 * reauthentication: not a staff role) and links the pending account to the
 * student profile (PUT …/account). Returns the invitation link's path
 * (`/invite#token=…`); it does not redeem it. Budget: 0 logins, 0
 * redemptions (opening and submitting the link spends them).
 */
export async function createStudentLogin(
  page: Page,
  {
    username,
    displayName,
    studentId,
  }: { username: string; displayName: string; studentId: string },
): Promise<string> {
  const invited = await api(page, '/api/v1/invitations', {
    method: 'POST',
    json: { username, display_name: displayName, role: 'student' },
    headers: { 'X-CSRF-Token': await csrf(page) },
  });
  expect(invited.status).toBe(201);
  const { user_id: userId, invitation_url: url } = invited.body as {
    user_id: string;
    invitation_url: string;
  };
  await linkAccount(page, studentId, userId);
  const link = new URL(url);
  return `${link.pathname}${link.hash}`;
}

/**
 * An active account without any token redemption (#35 budget): a signed-out
 * API context sends a self-registration request (`registration.ip`, see
 * REGISTRATIONS_PER_RUN), then the Owner's page session links the pending
 * account to `studentId` (a Student request must have a profile to pass the
 * gate) and approves the request through the approval inbox API (#40 G1). Budget: 0 logins, 0 redemptions, 1
 * registration. Returns the account's id.
 */
export async function createApprovedAccount(
  page: Page,
  request: APIRequest,
  baseURL: string,
  {
    username,
    displayName,
    password,
    role,
    studentId,
  }: {
    username: string;
    displayName: string;
    password: string;
    role: 'guardian' | 'student';
    studentId?: string;
  },
): Promise<string> {
  // SIGNED_OUT_STATE: a new API context would otherwise inherit the shared
  // Owner session (see support/users.ts).
  const context = await request.newContext({
    baseURL,
    storageState: SIGNED_OUT_STATE,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
  });
  try {
    const sent = await context.post('/api/v1/auth/registrations', {
      data: { username, display_name: displayName, password, role },
    });
    expect(sent.status()).toBe(202);
  } finally {
    await context.dispose();
  }
  const users = await api(
    page,
    `/api/v1/users?q=${encodeURIComponent(username)}&state=pending`,
  );
  expect(users.status).toBe(200);
  const user = (
    users.body as { items: { id: string; username: string }[] }
  ).items.find((item) => item.username === username);
  expect(user).toBeTruthy();
  if (studentId) {
    await linkAccount(page, studentId, user!.id);
  }
  const pending = await api(
    page,
    '/api/v1/approval-requests?request_type=identity.registration&status=pending&limit=100',
  );
  expect(pending.status).toBe(200);
  const item = (
    pending.body as { items: { id: string; subject: { username?: string } }[] }
  ).items.find((row) => row.subject.username === username);
  expect(item).toBeTruthy();
  const approved = await api(
    page,
    `/api/v1/approval-requests/${item!.id}/approve`,
    { method: 'POST', headers: { 'X-CSRF-Token': await csrf(page) } },
  );
  expect(approved.status).toBe(200);
  return user!.id;
}

/** Staff record a paper `student_login` consent (#35 K4, the a11y scan). */
export async function recordConsentViaApi(
  page: Page,
  studentId: string,
  guardianId: string,
): Promise<void> {
  const recorded = await api(page, `/api/v1/students/${studentId}/consents`, {
    method: 'POST',
    json: { scope: 'student_login', guardian_id: guardianId, method: 'paper' },
    headers: { 'X-CSRF-Token': await csrf(page) },
  });
  expect(recorded.status).toBe(201);
}
