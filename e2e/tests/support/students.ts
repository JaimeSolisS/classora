import { expect, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';

// Student profiles created through the real API (#33 L2) with the page's
// own session (the shared demo Owner state): no login. The page must be on
// the app origin first. Names carry the caller's unique suffix.

export async function createStudentViaApi(
  page: Page,
  {
    givenNames,
    familyNames,
    birthdate = '2013-02-14',
  }: {
    givenNames: string;
    familyNames: string;
    /** YYYY-MM-DD; the default makes a minor (#35 passes an adult's). */
    birthdate?: string;
  },
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const created = await api(page, '/api/v1/students', {
    method: 'POST',
    json: {
      given_names: givenNames,
      family_names: familyNames,
      birthdate,
      school_grade: '1.º',
      emergency_contacts: [
        { name: 'Contacto E2E', relationship: 'Tía', phone: '55 0000 0001' },
      ],
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}
