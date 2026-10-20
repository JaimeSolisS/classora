import { expect, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';

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
