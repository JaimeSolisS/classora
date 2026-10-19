import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { RoleCode } from '@/features/auth/session';

// The invitation calls (#29). The issue and reissue calls are direct calls,
// never TanStack mutations or queries: their response holds the one-time
// link, which must never enter a cache (I4). Only the list is a query.

export type InvitationIssued = components['schemas']['InvitationIssued'];
export type InvitationListItem = components['schemas']['InvitationListItem'];
export type InvitationPage =
  components['schemas']['OffsetPage_InvitationListItem_'];

export interface InvitationInput {
  username: string;
  display_name: string;
  role: RoleCode;
}

export const INVITATIONS_QUERY_KEY = ['invitations'] as const;

/** Rows per page of the open-invitations list. */
export const INVITATIONS_PAGE_SIZE = 10;

export function invitationsListKey(offset: number) {
  return [...INVITATIONS_QUERY_KEY, 'list', { offset }] as const;
}

export function fetchInvitations(
  offset: number,
  signal: AbortSignal,
): Promise<InvitationPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/invitations', {
        params: { query: { limit: INVITATIONS_PAGE_SIZE, offset } },
        signal,
      }),
    { signal },
  );
}

/** POST /invitations. A staff role opens the password dialog (#28 D). */
export function createInvitation(
  input: InvitationInput,
): Promise<InvitationIssued> {
  return apiCall(() => api.POST('/api/v1/invitations', { body: input }));
}

/** A new link for an open invitation; the earlier link stops working. */
export function reissueInvitation(userId: string): Promise<InvitationIssued> {
  return apiCall(() =>
    api.POST('/api/v1/invitations/{user_id}/reissue', {
      params: { path: { user_id: userId } },
    }),
  );
}
