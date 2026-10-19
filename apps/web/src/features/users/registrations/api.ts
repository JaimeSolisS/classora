import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The registration-request calls (#169 G). The list is a query; approve and
// reject are direct calls (their answer only refreshes the list).

export type RegistrationRequestItem =
  components['schemas']['RegistrationRequestItem'];
export type RegistrationRequestPage =
  components['schemas']['OffsetPage_RegistrationRequestItem_'];
export type RegistrationStatus = components['schemas']['RegistrationStatus'];
export type Decision = 'approve' | 'reject';

export const REGISTRATION_STATUSES: readonly RegistrationStatus[] = [
  'pending',
  'approved',
  'rejected',
];

export const REGISTRATION_REQUESTS_QUERY_KEY = [
  'registration-requests',
] as const;

/** Rows per page of the request list. */
export const REGISTRATION_REQUESTS_PAGE_SIZE = 10;

export function registrationRequestsKey(
  status: RegistrationStatus,
  offset: number,
) {
  return [
    ...REGISTRATION_REQUESTS_QUERY_KEY,
    'list',
    { status, offset },
  ] as const;
}

export function fetchRegistrationRequests(
  status: RegistrationStatus,
  offset: number,
  signal: AbortSignal,
): Promise<RegistrationRequestPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/registration-requests', {
        params: {
          query: { status, limit: REGISTRATION_REQUESTS_PAGE_SIZE, offset },
        },
        signal,
      }),
    { signal },
  );
}

/** Approve or reject one pending request. */
export function decideRegistration(
  id: string,
  decision: Decision,
): Promise<RegistrationRequestItem> {
  const params = { path: { request_id: id } };
  return apiCall(() =>
    decision === 'approve'
      ? api.POST('/api/v1/registration-requests/{request_id}/approve', {
          params,
        })
      : api.POST('/api/v1/registration-requests/{request_id}/reject', {
          params,
        }),
  );
}
