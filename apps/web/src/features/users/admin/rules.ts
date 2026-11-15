import type { RoleCode } from '@/features/auth/session';
import { ROLE_ORDER } from '../roles';
import {
  DELEGABLE_PERMISSIONS,
  USER_STATES,
  type DelegablePermission,
  type UserDetail,
  type UserListParams,
  type UserState,
  type UserUpdate,
} from './api';

// Pure helpers of the user administration pages (#31, #170): the list's URL
// parameters, the profile change body, the state-action checks and the role
// and delegation sets. UX only: the server computes `allowed_actions` and the
// routes decide.

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

/** `roles` in the catalog order (ROLE_ORDER), without duplicates. */
export function orderedRoles(roles: Iterable<RoleCode>): RoleCode[] {
  const held = new Set(roles);
  return ROLE_ORDER.filter((role) => held.has(role));
}

/** `permissions` in the API's order, without duplicates. */
export function orderedDelegations(
  permissions: Iterable<DelegablePermission>,
): DelegablePermission[] {
  const held = new Set(permissions);
  return DELEGABLE_PERMISSIONS.filter((code) => held.has(code));
}

/** Whether two sets of codes differ. */
export function setsDiffer<T>(a: readonly T[], b: readonly T[]): boolean {
  const left = new Set(a);
  const right = new Set(b);
  return left.size !== right.size || [...left].some((code) => !right.has(code));
}

/** The `common` catalog prefix of a delegable permission (`delegations.*`). */
export function delegationKey(
  code: DelegablePermission,
):
  | 'delegations.finance_refund_approve'
  | 'delegations.payroll_finalize'
  | 'delegations.schedule_override'
  | 'delegations.user_admin_role_assign' {
  switch (code) {
    case 'finance.refund.approve':
      return 'delegations.finance_refund_approve';
    case 'payroll.finalize':
      return 'delegations.payroll_finalize';
    case 'schedule.override':
      return 'delegations.schedule_override';
    case 'user.admin_role.assign':
      return 'delegations.user_admin_role_assign';
  }
}
