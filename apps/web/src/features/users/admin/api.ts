import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { RoleCode } from '@/features/auth/session';

// The user administration calls (#31). The list and the detail are queries;
// the profile change and the state changes are direct calls whose answer
// (the updated detail) replaces the detail in the cache.

export type UserListItem = components['schemas']['UserListItem'];
export type UserListPage = components['schemas']['OffsetPage_UserListItem_'];
export type UserDetail = components['schemas']['UserDetail'];
export type UserState = components['schemas']['UserState'];
export type UserUpdate = components['schemas']['UserUpdate'];
export type StateAction = 'suspend' | 'archive' | 'reactivate';

export const USER_STATES: readonly UserState[] = [
  'pending',
  'active',
  'suspended',
  'archived',
];

/** Rows per page of the user list (the API default). */
export const USERS_PAGE_SIZE = 25;

/** Every cached user query (list and detail). */
export const USERS_QUERY_KEY = ['users'] as const;
/** Every cached list page: invalidated after any change. */
export const USERS_LIST_QUERY_KEY = [...USERS_QUERY_KEY, 'list'] as const;

export interface UserListParams {
  q: string;
  role: RoleCode | '';
  state: UserState | '';
  /** 1-based page number. */
  page: number;
}

export function usersListKey(params: UserListParams) {
  return [...USERS_LIST_QUERY_KEY, params] as const;
}

export function userDetailKey(userId: string) {
  return [...USERS_QUERY_KEY, 'detail', userId] as const;
}

export function fetchUsers(
  { q, role, state, page }: UserListParams,
  signal: AbortSignal,
): Promise<UserListPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/users', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(role ? { role } : {}),
            ...(state ? { state } : {}),
            limit: USERS_PAGE_SIZE,
            offset: (page - 1) * USERS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchUser(
  userId: string,
  signal: AbortSignal,
): Promise<UserDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/users/{user_id}', {
        params: { path: { user_id: userId } },
        signal,
      }),
    { signal },
  );
}

/** PATCH the profile: `body` holds `version` and only the changed fields. */
export function updateUser(
  userId: string,
  body: UserUpdate,
): Promise<UserDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/users/{user_id}', {
      params: { path: { user_id: userId } },
      body,
    }),
  );
}

/**
 * Suspend, archive or reactivate with the loaded `version`. Suspend and
 * archive need a recent reauthentication, so apiCall opens the password
 * dialog and retries once (#28 D).
 */
export function changeUserState(
  userId: string,
  action: StateAction,
  version: number,
): Promise<UserDetail> {
  const init = { params: { path: { user_id: userId } }, body: { version } };
  return apiCall(() => {
    switch (action) {
      case 'suspend':
        return api.POST('/api/v1/users/{user_id}/suspend', init);
      case 'archive':
        return api.POST('/api/v1/users/{user_id}/archive', init);
      case 'reactivate':
        return api.POST('/api/v1/users/{user_id}/reactivate', init);
    }
  });
}
