import { expect, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';

// Notifications created through the local/DEV-only producer (#41 D1) with
// the page's own session (the shared demo Owner state): the recipient is
// the caller. The page must be on the app origin first. Labels carry the
// caller's unique suffix: other specs notify demo.owner too, so tests
// assert on their own label, never on counts.

export async function createDemoNotificationViaApi(
  page: Page,
  label: string,
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const created = await api(page, '/api/v1/dev/notifications', {
    method: 'POST',
    json: { label },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}
