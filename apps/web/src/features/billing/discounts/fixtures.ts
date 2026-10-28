import type { TargetOption } from '../targets';
import type { Discount, DiscountDetail, DiscountPage } from './api';

// Discount fixtures (#367 G1, G2) for the tests and the stories. The
// center's "today" in them is 2026-10-20.

export const SUBJECT_REF = {
  id: '30000000-0000-4000-8000-000000000001',
  name: 'Álgebra',
};
export const LEVEL_REF = {
  id: '20000000-0000-4000-8000-000000000002',
  name: 'Secundaria',
};

const NO_CRITERIA = {
  charge_kinds: null,
  class_types: null,
  subjects: null,
  education_levels: null,
  min_family_students: null,
};

export const SCHOLARSHIP: Discount = {
  id: '70000000-0000-4000-8000-000000000001',
  kind: 'scholarship',
  name: 'Beca excelencia',
  audience: 'assigned',
  percent: '50.0000',
  amount: null,
  eligibility: {
    ...NO_CRITERIA,
    charge_kinds: ['session', 'cohort_enrollment'],
    subjects: [SUBJECT_REF],
  },
  starts_on: '2026-08-01',
  expires_on: null,
  is_active: true,
  status: 'current',
  assignment_count: 2,
  version: 3,
  created_at: '2026-08-01T15:00:00Z',
};

export const SIBLING: Discount = {
  id: '70000000-0000-4000-8000-000000000002',
  kind: 'sibling',
  name: 'Hermanos',
  audience: 'all',
  percent: '12.5000',
  amount: null,
  eligibility: { ...NO_CRITERIA, min_family_students: 2 },
  starts_on: null,
  expires_on: null,
  is_active: true,
  status: 'current',
  assignment_count: 0,
  version: 1,
  created_at: '2026-08-01T15:00:00Z',
};

export const PROMOTION: Discount = {
  id: '70000000-0000-4000-8000-000000000003',
  kind: 'promotion',
  name: 'Buen Fin',
  audience: 'all',
  percent: null,
  amount: '100.00',
  eligibility: {
    ...NO_CRITERIA,
    class_types: ['individual'],
    education_levels: [LEVEL_REF],
  },
  starts_on: '2026-11-13',
  expires_on: '2026-11-16',
  is_active: true,
  status: 'upcoming',
  assignment_count: 0,
  version: 1,
  created_at: '2026-10-01T15:00:00Z',
};

export const RETIRED: Discount = {
  id: '70000000-0000-4000-8000-000000000004',
  kind: 'discount',
  name: 'Verano',
  audience: 'all',
  percent: '10.0000',
  amount: null,
  eligibility: NO_CRITERIA,
  starts_on: null,
  expires_on: '2026-08-31',
  is_active: false,
  status: 'inactive',
  assignment_count: 0,
  version: 2,
  created_at: '2026-06-01T15:00:00Z',
};

export const DISCOUNTS_PAGE: DiscountPage = {
  items: [SCHOLARSHIP, SIBLING, PROMOTION, RETIRED],
  total: 4,
  limit: 25,
  offset: 0,
};

export const EMPTY_PAGE: DiscountPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

export const STUDENT_ANA: TargetOption = {
  kind: 'student',
  id: '50000000-0000-4000-8000-000000000001',
  name: 'Ana López',
};
export const STUDENT_LUIS: TargetOption = {
  kind: 'student',
  id: '50000000-0000-4000-8000-000000000002',
  name: 'Luis Pérez',
};
export const FAMILY_LOPEZ: TargetOption = {
  kind: 'family',
  id: '60000000-0000-4000-8000-000000000001',
  name: 'Familia López',
};

export const SCHOLARSHIP_DETAIL: DiscountDetail = {
  ...SCHOLARSHIP,
  assignments: [
    {
      id: '71000000-0000-4000-8000-000000000001',
      student: { id: STUDENT_ANA.id, name: STUDENT_ANA.name },
      family: null,
      created_at: '2026-08-02T15:00:00Z',
    },
    {
      id: '71000000-0000-4000-8000-000000000002',
      student: null,
      family: { id: FAMILY_LOPEZ.id, name: FAMILY_LOPEZ.name },
      created_at: '2026-08-03T15:00:00Z',
    },
  ],
};

export const SIBLING_DETAIL: DiscountDetail = { ...SIBLING, assignments: [] };

/** The picker's search for the stories. */
export function searchFixtureTargets(
  kind: TargetOption['kind'],
): Promise<TargetOption[]> {
  return Promise.resolve(
    kind === 'student' ? [STUDENT_ANA, STUDENT_LUIS] : [FAMILY_LOPEZ],
  );
}
