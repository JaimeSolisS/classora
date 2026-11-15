import type { RoleCode } from '@/features/auth/session';
import type { UserDetail, UserListItem } from './api';

// Story fixtures of the user administration pages (#31 L3, #170 H3):
// synthetic people, no request.

/** What an Owner may assign: every role. */
export const OWNER_ASSIGNABLE: RoleCode[] = [
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
  'tutor',
  'student',
  'guardian',
];

/** What an Administrator without the delegation may assign. */
export const ADMIN_ASSIGNABLE: RoleCode[] = [
  'academic_coordinator',
  'finance_staff',
  'tutor',
  'student',
  'guardian',
];

export const TUTOR_ITEM: UserListItem = {
  id: '00000000-0000-4000-8000-0000000000d1',
  username: 'ana.tutor',
  display_name: 'Ana Tutor',
  state: 'active',
  roles: ['tutor', 'guardian'],
  created_at: '2026-10-01T15:00:00Z',
};

export const LIST_ITEMS: UserListItem[] = [
  TUTOR_ITEM,
  {
    id: '00000000-0000-4000-8000-0000000000d2',
    username: 'beto.ruiz',
    display_name: 'Beto Ruiz',
    state: 'suspended',
    roles: ['finance_staff'],
    created_at: '2026-09-12T15:00:00Z',
  },
  {
    id: '00000000-0000-4000-8000-0000000000d3',
    username: 'rosa.diaz',
    display_name: 'Rosa Díaz',
    state: 'pending',
    roles: [],
    created_at: '2026-10-18T15:00:00Z',
  },
];

export const ACTIVE_TUTOR: UserDetail = {
  ...TUTOR_ITEM,
  email: 'ana@example.com',
  phone: '+52 55 1234 5678',
  version: 3,
  updated_at: '2026-10-02T15:00:00Z',
  delegations: [],
  password_set: true,
  allowed_actions: {
    edit: true,
    suspend: true,
    archive: true,
    reactivate: false,
    reset_password: true,
    assignable_roles: OWNER_ASSIGNABLE,
    manage_delegations: false,
  },
};

export const OWN_RECORD: UserDetail = {
  ...ACTIVE_TUTOR,
  id: '00000000-0000-4000-8000-000000000001',
  username: 'demo.owner',
  display_name: 'Demo Owner',
  roles: ['owner'],
  email: null,
  phone: null,
  allowed_actions: {
    edit: true,
    suspend: false,
    archive: false,
    reactivate: false,
    reset_password: false,
    assignable_roles: OWNER_ASSIGNABLE,
    manage_delegations: false,
  },
};

export const PENDING_INVITATION: UserDetail = {
  ...ACTIVE_TUTOR,
  id: '00000000-0000-4000-8000-0000000000d4',
  username: 'luis.s',
  display_name: 'Luis S',
  state: 'pending',
  roles: ['tutor'],
  email: null,
  phone: null,
  password_set: false,
  allowed_actions: {
    edit: true,
    suspend: false,
    archive: true,
    reactivate: false,
    reset_password: false,
    assignable_roles: OWNER_ASSIGNABLE,
    manage_delegations: false,
  },
};

/** An active Tutor as an Administrator sees them (#170 H3). */
export const TUTOR_FOR_ADMINISTRATOR: UserDetail = {
  ...ACTIVE_TUTOR,
  allowed_actions: {
    ...ACTIVE_TUTOR.allowed_actions,
    reset_password: true,
    assignable_roles: ADMIN_ASSIGNABLE,
  },
};

/** An Administrator with one delegation, as an Owner sees them. */
export const DELEGATED_ADMINISTRATOR: UserDetail = {
  ...ACTIVE_TUTOR,
  id: '00000000-0000-4000-8000-0000000000d5',
  username: 'bea.admin',
  display_name: 'Bea Admin',
  roles: ['administrator'],
  delegations: ['finance.refund.approve'],
  allowed_actions: {
    ...ACTIVE_TUTOR.allowed_actions,
    reset_password: true,
    assignable_roles: OWNER_ASSIGNABLE,
    manage_delegations: true,
  },
};

/** The same Administrator as another Administrator sees them: read-only. */
export const ADMINISTRATOR_FOR_ADMINISTRATOR: UserDetail = {
  ...DELEGATED_ADMINISTRATOR,
  allowed_actions: {
    edit: false,
    suspend: false,
    archive: false,
    reactivate: false,
    reset_password: false,
    assignable_roles: [],
    manage_delegations: false,
  },
};
