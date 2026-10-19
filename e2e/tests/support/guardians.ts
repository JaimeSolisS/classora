import { expect, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';

// Guardian profiles created through the real API (#34 K2) with the page's
// own session (the shared demo Owner state): no login. The page must be on
// the app origin first. Names carry the caller's unique suffix.

export async function createGuardianViaApi(
  page: Page,
  {
    givenNames,
    familyNames,
    userId,
  }: {
    givenNames: string;
    familyNames: string;
    /** Links this login account (one holding the guardian role, #172). */
    userId?: string;
  },
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const created = await api(page, '/api/v1/guardians', {
    method: 'POST',
    json: {
      given_names: givenNames,
      family_names: familyNames,
      phone: '55 0000 0002',
      ...(userId ? { user_id: userId } : {}),
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/** Links a guardian to a student (relationship `mother`). */
export async function linkGuardianViaApi(
  page: Page,
  studentId: string,
  guardianId: string,
): Promise<void> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const linked = await api(page, `/api/v1/students/${studentId}/guardians`, {
    method: 'POST',
    json: { guardian_id: guardianId, relationship: 'mother' },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(linked.status).toBe(201);
}
