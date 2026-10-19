import type {
  AccountOption,
  GuardianDetail,
  GuardianListItem,
  GuardianListPage,
} from './api';

// Fixtures of the guardian pages (#34 I5, J3): synthetic people only. No
// request.

const AT = '2026-10-19T15:00:00Z';

export const LAURA_ID = '00000000-0000-4000-8000-0000000f0001';
export const ACCOUNT_ID = '00000000-0000-4000-8000-0000000a0001';

export const LAURA_ACCOUNT = {
  id: ACCOUNT_ID,
  username: 'laura.nunez',
  display_name: 'Laura Ñúñez',
  state: 'active',
} as const;

/** Laura as a family.manage holder sees her (with `account`). */
export const LAURA: GuardianDetail = {
  id: LAURA_ID,
  given_names: 'Laura',
  family_names: 'Ñúñez Prieto',
  email: 'laura@example.com',
  phone: '+52 00 1234 5678',
  is_active: true,
  has_account: true,
  account: { ...LAURA_ACCOUNT },
  students: [
    {
      student_id: '00000000-0000-4000-8000-0000000e0001',
      given_names: 'José',
      family_names: 'Ñúñez Pérez',
      relationship: 'mother',
      is_minor: true,
      is_active: true,
    },
    {
      student_id: '00000000-0000-4000-8000-0000000e0004',
      given_names: 'Sofía',
      family_names: 'Ñúñez Pérez',
      relationship: 'legal_guardian',
      is_minor: false,
      is_active: false,
    },
  ],
  version: 2,
  created_at: AT,
  updated_at: AT,
};

/** Laura's own view as a guardian: no `account`. */
export const LAURA_OWN_VIEW: GuardianDetail = { ...LAURA, account: null };

/** A profile without a login or contact data. */
export const RAMON: GuardianDetail = {
  ...LAURA,
  id: '00000000-0000-4000-8000-0000000f0002',
  given_names: 'Ramón',
  family_names: 'Pérez Solís',
  email: null,
  phone: null,
  has_account: false,
  account: null,
  students: [],
  version: 1,
};

export function listItem(
  guardian: GuardianDetail,
  overrides: Partial<GuardianListItem> = {},
): GuardianListItem {
  return {
    id: guardian.id,
    given_names: guardian.given_names,
    family_names: guardian.family_names,
    is_active: guardian.is_active,
    has_account: guardian.has_account,
    student_count: guardian.students.length,
    ...overrides,
  };
}

export const LIST_ITEMS: GuardianListItem[] = [
  listItem(LAURA),
  listItem(RAMON),
  listItem(RAMON, {
    id: '00000000-0000-4000-8000-0000000f0003',
    given_names: 'Inés',
    family_names: 'Zamora',
    is_active: false,
  }),
];

export function guardianPage(
  items: GuardianListItem[],
  total = items.length,
  offset = 0,
): GuardianListPage {
  return { items, total, limit: 25, offset };
}

/** Accounts the picker finds: one available, one archived. */
export const ACCOUNT_OPTIONS: AccountOption[] = [
  {
    id: '00000000-0000-4000-8000-0000000a0002',
    username: 'e2e.guardians.parent',
    display_name: 'Padre de prueba',
    state: 'pending',
    roles: ['guardian'],
    created_at: AT,
  },
  {
    id: '00000000-0000-4000-8000-0000000a0003',
    username: 'old.parent',
    display_name: 'Cuenta antigua',
    state: 'archived',
    roles: ['guardian'],
    created_at: AT,
  },
];
