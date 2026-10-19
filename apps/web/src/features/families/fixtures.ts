import { LAURA, RAMON } from '@/features/guardians/fixtures';
import { JOSE, NUNEZ_FAMILY_REF } from '@/features/students/fixtures';
import type {
  FamilyDetail,
  FamilyListItem,
  FamilyListPage,
  StudentOption,
} from './api';

// Fixtures of the family pages (#172 H5, I2): synthetic people only. No
// request.

const AT = '2026-10-19T15:00:00Z';

export const NUNEZ_ID = NUNEZ_FAMILY_REF.id;

/** The Ñúñez family as staff see it: two students, two guardians. */
export const NUNEZ: FamilyDetail = {
  id: NUNEZ_ID,
  name: NUNEZ_FAMILY_REF.name,
  is_active: true,
  students: [
    {
      student_id: JOSE.id,
      given_names: JOSE.given_names,
      family_names: JOSE.family_names,
      is_minor: true,
      is_active: true,
    },
    {
      student_id: '00000000-0000-4000-8000-0000000e0004',
      given_names: 'Sofía',
      family_names: 'Ñúñez Pérez',
      is_minor: false,
      is_active: false,
    },
  ],
  guardians: [
    {
      guardian_id: LAURA.id,
      given_names: LAURA.given_names,
      family_names: LAURA.family_names,
      phone: LAURA.phone,
      has_account: true,
      is_active: true,
    },
    {
      guardian_id: RAMON.id,
      given_names: RAMON.given_names,
      family_names: RAMON.family_names,
      phone: null,
      has_account: false,
      is_active: true,
    },
  ],
  version: 2,
  created_at: AT,
  updated_at: AT,
};

/** The same family as Laura (a guardian) sees it: only her own profile. */
export const NUNEZ_GUARDIAN_VIEW: FamilyDetail = {
  ...NUNEZ,
  guardians: NUNEZ.guardians.slice(0, 1),
};

/** A new family: no members. */
export const OTRA: FamilyDetail = {
  ...NUNEZ,
  id: '00000000-0000-4000-8000-0000000fa002',
  name: 'Familia Otra Prueba',
  students: [],
  guardians: [],
  version: 1,
};

export function listItem(
  family: FamilyDetail,
  overrides: Partial<FamilyListItem> = {},
): FamilyListItem {
  return {
    id: family.id,
    name: family.name,
    is_active: family.is_active,
    student_count: family.students.length,
    ...overrides,
  };
}

export const LIST_ITEMS: FamilyListItem[] = [
  listItem(NUNEZ),
  listItem(OTRA),
  listItem(OTRA, {
    id: '00000000-0000-4000-8000-0000000fa003',
    name: 'Familia Zamora',
    is_active: false,
  }),
];

export function familyPage(
  items: FamilyListItem[],
  total = items.length,
  offset = 0,
): FamilyListPage {
  return { items, total, limit: 25, offset };
}

/** Students the picker finds. */
export const STUDENT_OPTIONS: StudentOption[] = [
  {
    id: '00000000-0000-4000-8000-0000000e0005',
    given_names: 'Mateo',
    family_names: 'Ríos Luna',
    is_active: true,
    is_minor: true,
    school_grade: null,
    education_level: null,
  },
];
