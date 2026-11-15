import type {
  UnbilledEnrollment,
  UnbilledEnrollmentPage,
  UnbilledItem,
  UnbilledPage,
  UnbilledTuition,
  UnbilledTuitionPage,
} from './api';

// Unbilled session fixtures (#366 E1, E2), unbilled enrollment fixtures
// (#372 D1, D2) and unbilled tuition period fixtures (#374 H1, H2) for the
// tests and the stories.

const SUBJECT_ALGEBRA = {
  id: '30000000-0000-4000-8000-000000000001',
  name: 'Álgebra',
};

/** A student without a family (reason no_billing_account). */
export const ITEM_NO_FAMILY: UnbilledItem = {
  class_session_id: '60000000-0000-4000-8000-000000000001',
  // 10:00 in Mexico City.
  starts_at: '2026-10-05T16:00:00Z',
  ends_at: '2026-10-05T17:00:00Z',
  service_date: '2026-10-05',
  state: 'completed',
  scheduled_minutes: 60,
  subject: SUBJECT_ALGEBRA,
  student: { id: '40000000-0000-4000-8000-000000000001', name: 'Ana López' },
  education_level: {
    id: '20000000-0000-4000-8000-000000000002',
    name: 'Secundaria',
  },
  family: null,
  reason: 'no_billing_account',
  class_type: 'individual',
  group: null,
};

/** A session with no price in effect on its date (reason no_price). */
export const ITEM_NO_PRICE: UnbilledItem = {
  class_session_id: '60000000-0000-4000-8000-000000000002',
  // 17:30 in Mexico City.
  starts_at: '2026-10-06T23:30:00Z',
  ends_at: '2026-10-07T00:15:00Z',
  service_date: '2026-10-06',
  state: 'confirmed',
  scheduled_minutes: 45,
  subject: { id: '30000000-0000-4000-8000-000000000002', name: 'Física' },
  student: { id: '40000000-0000-4000-8000-000000000002', name: 'Bruno Díaz' },
  education_level: null,
  family: { id: '50000000-0000-4000-8000-000000000001', name: 'Familia Díaz' },
  reason: 'no_price',
  class_type: 'individual',
  group: null,
};

/** A group session of a student without a family. */
export const ITEM_GROUP: UnbilledItem = {
  class_session_id: '60000000-0000-4000-8000-000000000003',
  // 16:00 in Mexico City.
  starts_at: '2026-10-07T22:00:00Z',
  ends_at: '2026-10-07T23:30:00Z',
  service_date: '2026-10-07',
  state: 'confirmed',
  scheduled_minutes: 90,
  subject: SUBJECT_ALGEBRA,
  student: { id: '40000000-0000-4000-8000-000000000003', name: 'Carla Ruiz' },
  education_level: null,
  family: null,
  reason: 'no_billing_account',
  class_type: 'group',
  group: { id: '70000000-0000-4000-8000-000000000001', name: 'Álgebra tarde' },
};

export const UNBILLED_PAGE: UnbilledPage = {
  items: [ITEM_NO_FAMILY, ITEM_NO_PRICE, ITEM_GROUP],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_UNBILLED_PAGE: UnbilledPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

/** A flat-fee group enrollment of a student without a family. */
export const ENROLLMENT_GROUP_NO_FAMILY: UnbilledEnrollment = {
  kind: 'group_enrollment',
  enrollment_id: '80000000-0000-4000-8000-000000000001',
  // 23:00 on Oct 1 in Mexico City.
  enrolled_at: '2026-10-02T05:00:00Z',
  withdrawn_at: null,
  service_date: '2026-10-01',
  student: { id: '40000000-0000-4000-8000-000000000003', name: 'Carla Ruiz' },
  family: null,
  education_level: null,
  subject: SUBJECT_ALGEBRA,
  group: {
    id: '70000000-0000-4000-8000-000000000002',
    name: 'Álgebra intensivo',
  },
  cohort: null,
  reason: 'no_billing_account',
};

/** A course enrollment with no course fee on its date. */
export const ENROLLMENT_COHORT_NO_PRICE: UnbilledEnrollment = {
  kind: 'cohort_enrollment',
  enrollment_id: '80000000-0000-4000-8000-000000000002',
  enrolled_at: '2026-10-03T17:00:00Z',
  withdrawn_at: null,
  service_date: '2026-10-03',
  student: { id: '40000000-0000-4000-8000-000000000002', name: 'Bruno Díaz' },
  family: { id: '50000000-0000-4000-8000-000000000001', name: 'Familia Díaz' },
  education_level: {
    id: '20000000-0000-4000-8000-000000000003',
    name: 'Preparatoria',
  },
  subject: { id: '30000000-0000-4000-8000-000000000002', name: 'Física' },
  group: null,
  cohort: {
    id: '90000000-0000-4000-8000-000000000001',
    name: 'Física I otoño',
  },
  reason: 'no_price',
};

/** A course enrollment of a student without a family. */
export const ENROLLMENT_COHORT_NO_FAMILY: UnbilledEnrollment = {
  ...ENROLLMENT_COHORT_NO_PRICE,
  enrollment_id: '80000000-0000-4000-8000-000000000003',
  student: { id: '40000000-0000-4000-8000-000000000004', name: 'Diego Mora' },
  family: null,
  reason: 'no_billing_account',
};

export const UNBILLED_ENROLLMENTS_PAGE: UnbilledEnrollmentPage = {
  items: [
    ENROLLMENT_GROUP_NO_FAMILY,
    ENROLLMENT_COHORT_NO_PRICE,
    ENROLLMENT_COHORT_NO_FAMILY,
  ],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_UNBILLED_ENROLLMENTS_PAGE: UnbilledEnrollmentPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

/** An individual monthly plan's period with no price in effect. */
export const TUITION_NO_PRICE: UnbilledTuition = {
  tuition_plan_id: 'a0000000-0000-4000-8000-000000000001',
  period_start: '2026-10-01',
  period_end: '2026-10-31',
  frequency: 'monthly',
  student: { id: '40000000-0000-4000-8000-000000000001', name: 'Ana López' },
  family: { id: '50000000-0000-4000-8000-000000000002', name: 'Familia López' },
  subject: SUBJECT_ALGEBRA,
  group: null,
  reason: 'no_price',
};

/** A group plan's period after the student moved to another family. */
export const TUITION_FAMILY_CHANGED: UnbilledTuition = {
  tuition_plan_id: 'a0000000-0000-4000-8000-000000000002',
  period_start: '2026-10-05',
  period_end: '2026-10-18',
  frequency: 'biweekly',
  student: { id: '40000000-0000-4000-8000-000000000002', name: 'Bruno Díaz' },
  family: null,
  subject: SUBJECT_ALGEBRA,
  group: {
    id: '70000000-0000-4000-8000-000000000001',
    name: 'Álgebra sábados',
  },
  reason: 'family_changed',
};

export const UNBILLED_TUITION_PAGE: UnbilledTuitionPage = {
  items: [TUITION_NO_PRICE, TUITION_FAMILY_CHANGED],
  total: 2,
  limit: 25,
  offset: 0,
};

export const EMPTY_UNBILLED_TUITION_PAGE: UnbilledTuitionPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};
