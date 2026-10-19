import type {
  EducationLevelReference,
  StudentDetail,
  StudentListItem,
  StudentListPage,
} from './api';

// Fixtures of the student pages (#33 I4, J4, K4): synthetic people only. No
// request.

const AT = '2026-10-19T15:00:00Z';

export const SECUNDARIA_REF: EducationLevelReference = {
  id: '00000000-0000-4000-8000-00000000c002',
  name: 'Secundaria',
  is_active: true,
};
export const PRIMARIA_REF: EducationLevelReference = {
  id: '00000000-0000-4000-8000-00000000c001',
  name: 'Primaria',
  is_active: true,
};
export const POSGRADO_REF: EducationLevelReference = {
  id: '00000000-0000-4000-8000-00000000c004',
  name: 'Posgrado',
  is_active: false,
};

export const JOSE_ID = '00000000-0000-4000-8000-0000000e0001';

/** The family José and Laura belong to (#172). */
export const NUNEZ_FAMILY_REF = {
  id: '00000000-0000-4000-8000-0000000fa001',
  name: 'Familia Ñúñez Pérez',
} as const;

export const JOSE: StudentDetail = {
  id: JOSE_ID,
  given_names: 'José',
  family_names: 'Ñúñez Pérez',
  is_active: true,
  is_minor: true,
  school_name: 'Escuela Secundaria Técnica 1',
  school_grade: '2.º de secundaria',
  education_level: SECUNDARIA_REF,
  personal: {
    birthdate: '2012-05-04',
    email: 'jose@example.com',
    phone: '55 1234 5678',
    emergency_contacts: [
      {
        id: '00000000-0000-4000-8000-0000000ec001',
        name: 'María Pérez',
        relationship: 'Madre',
        phone: '+52 55 1234 5678',
        position: 1,
      },
      {
        id: '00000000-0000-4000-8000-0000000ec002',
        name: 'Luis Ñúñez',
        relationship: 'Padre',
        phone: '55 8765 4321',
        position: 2,
      },
    ],
  },
  guardians: [
    {
      guardian_id: '00000000-0000-4000-8000-0000000f0001',
      given_names: 'Laura',
      family_names: 'Ñúñez Prieto',
      relationship: 'mother',
      phone: '+52 00 1234 5678',
      is_active: true,
      has_account: true,
    },
    {
      guardian_id: '00000000-0000-4000-8000-0000000f0002',
      given_names: 'Ramón',
      family_names: 'Pérez Solís',
      relationship: 'grandparent',
      phone: null,
      is_active: false,
      has_account: false,
    },
  ],
  family: { ...NUNEZ_FAMILY_REF },
  version: 3,
  created_at: AT,
  updated_at: AT,
};

/** José as an assigned tutor sees him (limited, no guardians or family). */
export const JOSE_LIMITED: StudentDetail = {
  ...JOSE,
  personal: null,
  guardians: null,
  family: null,
};

/** A student whose level was deactivated after it was assigned. */
export const ANA_INACTIVE_LEVEL: StudentDetail = {
  ...JOSE,
  id: '00000000-0000-4000-8000-0000000e0002',
  given_names: 'Ana',
  family_names: 'García',
  is_minor: false,
  education_level: POSGRADO_REF,
  family: null,
  personal: {
    birthdate: '2001-03-03',
    email: null,
    phone: null,
    emergency_contacts: [
      {
        id: '00000000-0000-4000-8000-0000000ec003',
        name: 'Rosa García',
        relationship: 'Tía',
        phone: '55 2222 3333',
        position: 1,
      },
    ],
  },
  version: 1,
};

export const FIVE_CONTACTS: StudentDetail = {
  ...JOSE,
  personal: {
    ...JOSE.personal!,
    emergency_contacts: [1, 2, 3, 4, 5].map((n) => ({
      id: `00000000-0000-4000-8000-0000000ec10${n}`,
      name: `Contacto ${n}`,
      relationship: 'Vecina',
      phone: `55 1000 000${n}`,
      position: n,
    })),
  },
};

export function listItem(
  student: StudentDetail,
  overrides: Partial<StudentListItem> = {},
): StudentListItem {
  return {
    id: student.id,
    given_names: student.given_names,
    family_names: student.family_names,
    is_active: student.is_active,
    is_minor: student.is_minor,
    school_grade: student.school_grade,
    education_level: student.education_level,
    ...overrides,
  };
}

export const LIST_ITEMS: StudentListItem[] = [
  listItem(ANA_INACTIVE_LEVEL),
  listItem(JOSE),
  listItem(JOSE, {
    id: '00000000-0000-4000-8000-0000000e0003',
    given_names: 'Mateo',
    family_names: 'Zamora',
    is_active: false,
    is_minor: false,
    school_grade: null,
    education_level: null,
  }),
];

export function studentPage(
  items: StudentListItem[],
  total = items.length,
  offset = 0,
): StudentListPage {
  return { items, total, limit: 25, offset };
}

/** The active levels the selects offer (catalog list shape). */
export const ACTIVE_LEVELS = {
  items: [PRIMARIA_REF, SECUNDARIA_REF].map((level, index) => ({
    ...level,
    sort_order: (index + 1) * 10,
    active_subject_count: 0,
    version: 1,
    created_at: AT,
    updated_at: AT,
  })),
  total: 2,
  limit: 100,
  offset: 0,
};
