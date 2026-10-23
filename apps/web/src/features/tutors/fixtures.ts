import type {
  AvailabilityChangeRequest,
  AvailabilityException,
  CatalogOption,
  QualificationItem,
  TeachingSubject,
  TutorDetail,
  TutorListItem,
  TutorListPage,
  TutorAvailability,
} from './api';

// Sample tutor profiles for the tests and stories (#43). Placeholder
// contact data only.

export const TUTOR_ID = '00000000-0000-4000-8000-0000000000c1';

export const TUTOR_ITEM: TutorListItem = {
  user_id: TUTOR_ID,
  username: 'lucia.tutora',
  display_name: 'Lucía Méndez',
  state: 'active',
  email: 'lucia@example.com',
  phone: '55 1234 5678',
};

export const TUTOR_ITEMS: TutorListItem[] = [
  TUTOR_ITEM,
  {
    user_id: '00000000-0000-4000-8000-0000000000c2',
    username: 'pablo.tutor',
    display_name: 'Pablo Ortega',
    state: 'suspended',
    email: null,
    phone: '55 8765 4321',
  },
  {
    user_id: '00000000-0000-4000-8000-0000000000c3',
    username: 'invited.tutor',
    display_name: 'Invited Tutor',
    state: 'pending',
    email: 'invited@example.com',
    phone: null,
  },
];

export function tutorPage(
  items: TutorListItem[] = TUTOR_ITEMS,
  total = items.length,
  offset = 0,
): TutorListPage {
  return { items, total, limit: 25, offset };
}

export const LEVEL_SECUNDARIA: CatalogOption = {
  id: '00000000-0000-4000-8000-0000000000d1',
  name: 'Secundaria',
};
export const LEVEL_PREPARATORIA: CatalogOption = {
  id: '00000000-0000-4000-8000-0000000000d2',
  name: 'Preparatoria',
};
export const SUBJECT_ALGEBRA: CatalogOption = {
  id: '00000000-0000-4000-8000-0000000000e1',
  name: 'Álgebra',
};
export const SUBJECT_FISICA: CatalogOption = {
  id: '00000000-0000-4000-8000-0000000000e2',
  name: 'Física',
};
export const SUBJECT_CALCULO: CatalogOption = {
  id: '00000000-0000-4000-8000-0000000000e3',
  name: 'Cálculo',
};

/** The tutor's approved subjects (#44 D1). */
export const TEACHING_SUBJECTS: TeachingSubject[] = [
  {
    subject_id: SUBJECT_ALGEBRA.id,
    subject_name: SUBJECT_ALGEBRA.name,
    education_level_id: LEVEL_SECUNDARIA.id,
    education_level_name: LEVEL_SECUNDARIA.name,
    subject_is_active: true,
  },
];

function qualification(
  id: string,
  subject: CatalogOption,
  level: CatalogOption,
  overrides: Partial<QualificationItem>,
): QualificationItem {
  return {
    id,
    subject_id: subject.id,
    subject_name: subject.name,
    education_level_id: level.id,
    education_level_name: level.name,
    subject_is_active: true,
    status: 'pending',
    requested_at: '2026-10-01T15:00:00Z',
    decided_at: null,
    rejection_reason: null,
    revoked_at: null,
    revocation_reason: null,
    version: 1,
    ...overrides,
  };
}

/** One qualification in each status (#44 F4 "mixed statuses"). */
export const QUALIFICATIONS: QualificationItem[] = [
  qualification(
    '00000000-0000-4000-8000-0000000000f1',
    SUBJECT_ALGEBRA,
    LEVEL_SECUNDARIA,
    {
      status: 'approved',
      decided_at: '2026-10-02T16:30:00Z',
      version: 2,
    },
  ),
  qualification(
    '00000000-0000-4000-8000-0000000000f2',
    SUBJECT_FISICA,
    LEVEL_SECUNDARIA,
    {},
  ),
  qualification(
    '00000000-0000-4000-8000-0000000000f3',
    SUBJECT_CALCULO,
    LEVEL_PREPARATORIA,
    {
      status: 'rejected',
      decided_at: '2026-10-03T17:00:00Z',
      rejection_reason: 'Please add a certificate for calculus first.',
      version: 2,
    },
  ),
  qualification(
    '00000000-0000-4000-8000-0000000000f4',
    SUBJECT_FISICA,
    LEVEL_PREPARATORIA,
    {
      status: 'cancelled',
      decided_at: '2026-10-04T18:00:00Z',
      version: 2,
    },
  ),
];

/** A revoked qualification (#198): approved, then withdrawn. */
export const REVOKED_QUALIFICATION: QualificationItem = qualification(
  '00000000-0000-4000-8000-0000000000f5',
  SUBJECT_CALCULO,
  LEVEL_SECUNDARIA,
  {
    status: 'revoked',
    decided_at: '2026-10-02T16:30:00Z',
    revoked_at: '2026-10-05T19:00:00Z',
    revocation_reason: 'The certificate has expired.',
    version: 3,
  },
);

/** QUALIFICATIONS plus the revoked one (#198 E4). */
export const QUALIFICATIONS_WITH_REVOKED: QualificationItem[] = [
  ...QUALIFICATIONS,
  REVOKED_QUALIFICATION,
];

/** As an Owner or Administrator sees it: every field editable. */
export const TUTOR_AS_ADMIN: TutorDetail = {
  ...TUTOR_ITEM,
  professional_experience:
    'Eight years teaching mathematics.\nOlympiad coach since 2021.',
  version: 2,
  editable_fields: [
    'display_name',
    'email',
    'phone',
    'professional_experience',
  ],
  teaching_subjects: TEACHING_SUBJECTS,
};

/** As the tutor sees their own profile: no display name. */
export const TUTOR_AS_SELF: TutorDetail = {
  ...TUTOR_AS_ADMIN,
  editable_fields: ['email', 'phone', 'professional_experience'],
};

/** As an Academic Coordinator sees it: read only. */
export const TUTOR_READ_ONLY: TutorDetail = {
  ...TUTOR_AS_ADMIN,
  editable_fields: [],
};

/** A tutor without a stored profile (version 0). */
export const NEW_TUTOR: TutorDetail = {
  ...TUTOR_ITEM,
  email: null,
  professional_experience: null,
  version: 0,
  editable_fields: [
    'display_name',
    'email',
    'phone',
    'professional_experience',
  ],
  teaching_subjects: [],
};

// --- Availability (#45) ------------------------------------------------------

export const EXCEPTIONS: AvailabilityException[] = [
  {
    id: '00000000-0000-4000-8000-0000000000f1',
    kind: 'unavailable',
    starts_on: '2026-12-21',
    ends_on: '2026-12-31',
    starts_at: null,
    ends_at: null,
    note: 'Vacaciones',
    created_at: '2026-10-01T15:00:00Z',
  },
  {
    id: '00000000-0000-4000-8000-0000000000f2',
    kind: 'available',
    starts_on: '2027-01-09',
    ends_on: '2027-01-09',
    starts_at: '10:00',
    ends_at: '12:00',
    note: null,
    created_at: '2026-10-02T15:00:00Z',
  },
];

export const AVAILABILITY: TutorAvailability = {
  weekly: [
    { weekday: 1, starts_at: '16:00', ends_at: '20:00' },
    { weekday: 3, starts_at: '16:00', ends_at: '18:00' },
    { weekday: 3, starts_at: '18:00', ends_at: '19:00' },
  ],
  exceptions: EXCEPTIONS,
  limits: {
    daily_limit_minutes: 240,
    weekly_limit_minutes: 1200,
    required_break_minutes: 15,
  },
  preferred_classrooms: [
    {
      id: '00000000-0000-4000-8000-0000000000c1',
      name: 'Aula 1',
      is_active: true,
    },
    {
      id: '00000000-0000-4000-8000-0000000000c3',
      name: 'Aula Vieja',
      is_active: false,
    },
  ],
  version: 2,
  pending_changes: [],
  recent_changes: [],
};

/** The active classrooms offered as preferred rooms (#197). */
export const CLASSROOM_OPTIONS: CatalogOption[] = [
  { id: '00000000-0000-4000-8000-0000000000c1', name: 'Aula 1' },
  { id: '00000000-0000-4000-8000-0000000000c2', name: 'Aula 2' },
];

export const NO_AVAILABILITY: TutorAvailability = {
  weekly: [],
  exceptions: [],
  limits: {
    daily_limit_minutes: null,
    weekly_limit_minutes: null,
    required_break_minutes: null,
  },
  preferred_classrooms: [],
  version: 0,
  pending_changes: [],
  recent_changes: [],
};

// --- Availability change requests (M4-18 #63) ---------------------------------

const AFFECTED: AvailabilityChangeRequest['affected_sessions'] = Array.from(
  { length: 5 },
  (_, week) => ({
    id: `00000000-0000-4000-8000-0000000009${String(week).padStart(2, '0')}`,
    starts_at: `2027-01-${String(4 + week * 7).padStart(2, '0')}T22:00:00Z`,
    ends_at: `2027-01-${String(4 + week * 7).padStart(2, '0')}T23:00:00Z`,
    subject_name: 'Física',
    state: week === 0 ? ('offered' as const) : ('confirmed' as const),
  }),
);

/** A weekly change waiting for approval (the tutor's own). */
export const WEEKLY_CHANGE: AvailabilityChangeRequest = {
  id: '00000000-0000-4000-8000-0000000009c1',
  kind: 'weekly',
  status: 'pending',
  change: {
    windows: [
      { weekday: 1, starts_at: '16:00', ends_at: '18:00' },
      { weekday: 3, starts_at: '16:00', ends_at: '19:00' },
    ],
    exception: null,
  },
  affected_count: 5,
  affected_sessions: AFFECTED,
  requested_by: { id: TUTOR_ID, display_name: 'Carmen Ruiz' },
  created_at: '2026-10-21T15:00:00Z',
  resolved_at: null,
  rejection_reason: null,
  approval_request_id: '00000000-0000-4000-8000-0000000009a1',
  version: 1,
};

/** Time off waiting for approval, sent by an Administrator. */
export const TIME_OFF_CHANGE: AvailabilityChangeRequest = {
  ...WEEKLY_CHANGE,
  id: '00000000-0000-4000-8000-0000000009c2',
  kind: 'exception_create',
  change: {
    windows: null,
    exception: {
      id: null,
      kind: 'unavailable',
      starts_on: '2027-01-11',
      ends_on: '2027-01-15',
      starts_at: null,
      ends_at: null,
      note: 'Congreso',
    },
  },
  affected_count: 1,
  affected_sessions: AFFECTED.slice(1, 2),
  requested_by: {
    id: '00000000-0000-4000-8000-0000000000a1',
    display_name: 'Laura Admin',
  },
  approval_request_id: '00000000-0000-4000-8000-0000000009a2',
};

/** A rejected change, with its reason. */
export const REJECTED_CHANGE: AvailabilityChangeRequest = {
  ...WEEKLY_CHANGE,
  id: '00000000-0000-4000-8000-0000000009c3',
  kind: 'exception_delete',
  status: 'rejected',
  change: {
    windows: null,
    exception: {
      id: EXCEPTIONS[1]!.id,
      kind: 'available',
      starts_on: '2027-01-09',
      ends_on: '2027-01-09',
      starts_at: '10:00',
      ends_at: '12:00',
      note: null,
    },
  },
  affected_count: 2,
  affected_sessions: AFFECTED.slice(0, 2),
  resolved_at: '2026-10-22T10:00:00Z',
  rejection_reason: 'No hay sustituto para esas clases.',
  approval_request_id: '00000000-0000-4000-8000-0000000009a3',
  version: 2,
};

/** The availability with two pending changes and a rejected one. */
export const AVAILABILITY_WITH_CHANGES: TutorAvailability = {
  ...AVAILABILITY,
  pending_changes: [WEEKLY_CHANGE, TIME_OFF_CHANGE],
  recent_changes: [REJECTED_CHANGE],
};
