import type { RoleCode } from '@/features/auth/session';
import { ROLE_ORDER } from '../roles';
import {
  USER_STATES,
  type UserDetail,
  type UserListParams,
  type UserState,
  type UserUpdate,
} from './api';

// Pure helpers of the user administration pages (#31): the list's URL
// parameters, the profile change body and the state-action checks. UX only:
// the server computes `allowed_actions` and the routes decide.

/** The API's limit on `q`. */
export const SEARCH_MAX_LENGTH = 100;

/** The list parameters in `search`; anything invalid is ignored. */
export function listParams(search: URLSearchParams): UserListParams {
  const role = search.get('role') ?? '';
  const state = search.get('state') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    role: (ROLE_ORDER as readonly string[]).includes(role)
      ? (role as RoleCode)
      : '',
    state: (USER_STATES as readonly string[]).includes(state)
      ? (state as UserState)
      : '',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** The profile form's values (trimmed by the schema). */
export interface ProfileValues {
  displayName: string;
  email: string;
  phone: string;
}

/** `version` plus the fields that differ from `user` (empty clears). */
export function profileChanges(
  user: UserDetail,
  values: ProfileValues,
): UserUpdate {
  const body: UserUpdate = { version: user.version };
  if (values.displayName !== user.display_name) {
    body.display_name = values.displayName;
  }
  if (values.email !== (user.email ?? '')) {
    body.email = values.email === '' ? null : values.email;
  }
  if (values.phone !== (user.phone ?? '')) {
    body.phone = values.phone === '' ? null : values.phone;
  }
  return body;
}

/** Whether the user is an open invitation (pending, no password). */
export function isOpenInvitation(user: UserDetail): boolean {
  return user.state === 'pending' && !user.password_set;
}

/** True when the account status section has a button for this user. */
export function hasStateActions(user: UserDetail): boolean {
  const { suspend, archive, reactivate } = user.allowed_actions;
  return suspend || archive || reactivate;
}
