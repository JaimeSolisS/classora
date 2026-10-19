import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The consent calls (#35 D) and the student's login account (#35 C). The
// overview is a query; record, revoke and the account link are direct calls,
// after which the page refetches the overview and the student detail.

export type ConsentOverview = components['schemas']['ConsentOverview'];
export type ConsentRecordItem = components['schemas']['ConsentRecordItem'];
export type ConsentGuardianOption =
  components['schemas']['ConsentGuardianOption'];
export type ConsentCreate = components['schemas']['ConsentCreate'];
export type ConsentRevocation = components['schemas']['ConsentRevocation'];
export type ConsentScope = components['schemas']['ConsentScope'];
export type ConsentMethod = components['schemas']['ConsentMethod'];
export type StudentLogin = components['schemas']['StudentLogin'];
export type StudentAccountUpdate =
  components['schemas']['StudentAccountUpdate'];
export type LoginAccountOption = components['schemas']['UserListItem'];

/** How many accounts the login picker shows. */
export const ACCOUNT_PICKER_LIMIT = 10;

/** Every cached consent overview. */
export const CONSENTS_QUERY_KEY = ['consents'] as const;

export function consentsKey(studentId: string) {
  return [...CONSENTS_QUERY_KEY, studentId] as const;
}

export function fetchConsents(
  studentId: string,
  signal: AbortSignal,
): Promise<ConsentOverview> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/consents', {
        params: { path: { student_id: studentId } },
        signal,
      }),
    { signal },
  );
}

export function recordConsent(
  studentId: string,
  body: ConsentCreate,
): Promise<ConsentRecordItem> {
  return apiCall(() =>
    api.POST('/api/v1/students/{student_id}/consents', {
      params: { path: { student_id: studentId } },
      body,
    }),
  );
}

export function revokeConsent(
  studentId: string,
  consentId: string,
): Promise<ConsentRevocation> {
  return apiCall(() =>
    api.POST('/api/v1/students/{student_id}/consents/{consent_id}/revoke', {
      params: { path: { student_id: studentId, consent_id: consentId } },
    }),
  );
}

/**
 * Up to 10 accounts matching `q` (the login picker; any role, archived ones
 * listed and disabled). Every family.manage holder also holds user.view.
 */
export async function searchLoginAccounts(
  q: string,
  signal: AbortSignal,
): Promise<LoginAccountOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/users', {
        params: { query: { q, limit: ACCOUNT_PICKER_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items;
}

/** Link a login account to the student, or unlink it (`user_id: null`). */
export function setStudentAccount(
  studentId: string,
  body: StudentAccountUpdate,
) {
  return apiCall(() =>
    api.PUT('/api/v1/students/{student_id}/account', {
      params: { path: { student_id: studentId } },
      body,
    }),
  );
}
