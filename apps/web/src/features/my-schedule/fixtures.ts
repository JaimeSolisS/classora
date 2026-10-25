import type { LocalDate } from '@/lib/time';
import type {
  FamilyScheduleItem,
  FamilyStudent,
  FamilyStudents,
  TutorScheduleItem,
} from './api';

// Sample data for the "My schedule" tests and stories (#67 G6, G7). The week
// of Monday 2026-11-09 in America/Mexico_City (UTC-6, no DST).

export const ZONE = 'America/Mexico_City';
export const WEEK: LocalDate = '2026-11-09' as LocalDate;
export const TUTOR_ID = '00000000-0000-4000-8000-000000067a01';

const MATH = {
  id: '00000000-0000-4000-8000-000000067b01',
  name: 'Matemáticas',
  education_level_name: 'Secundaria',
};
const PHYSICS = {
  id: '00000000-0000-4000-8000-000000067b02',
  name: 'Física',
  education_level_name: 'Preparatoria',
};

export const LUCIA: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000067c01',
  display_name: 'Lucía Pérez',
  relation: 'guardian',
  is_minor: true,
  schedule_access: 'allowed',
};
export const MATEO: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000067c02',
  display_name: 'Mateo Pérez',
  relation: 'guardian',
  is_minor: false,
  schedule_access: 'consent_required',
};
export const SELF: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000067c03',
  display_name: 'Ana López',
  relation: 'self',
  is_minor: false,
  schedule_access: 'allowed',
};

export function familyStudents(...items: FamilyStudent[]): FamilyStudents {
  return { items };
}

/** "2026-11-09T16:00:00Z" style instants: `day` 0-6 of WEEK, local hour. */
function at(day: number, hour: number): string {
  const date = new Date(Date.UTC(2026, 10, 9 + day, hour + 6));
  return date.toISOString().replace('.000Z', 'Z');
}

export function familyItem(
  overrides: Partial<FamilyScheduleItem> = {},
): FamilyScheduleItem {
  return {
    id: '00000000-0000-4000-8000-000000067d01',
    kind: 'individual',
    state: 'confirmed',
    starts_at: at(0, 10),
    ends_at: at(0, 11),
    subject: MATH,
    tutor: { display_name: 'Carmen Ruiz' },
    classroom: { name: 'Aula 2' },
    attendance: null,
    ...overrides,
  };
}

/** One item per family state (#67 G7). */
export const FAMILY_ITEMS: FamilyScheduleItem[] = [
  familyItem(),
  familyItem({
    id: '00000000-0000-4000-8000-000000067d02',
    state: 'completed',
    starts_at: at(1, 9),
    ends_at: at(1, 10),
    subject: PHYSICS,
    classroom: null,
  }),
  familyItem({
    id: '00000000-0000-4000-8000-000000067d03',
    state: 'cancelled',
    starts_at: at(2, 16),
    ends_at: at(2, 17),
  }),
  familyItem({
    id: '00000000-0000-4000-8000-000000067d04',
    kind: 'group',
    state: 'skipped_closure',
    starts_at: at(3, 12),
    ends_at: at(3, 13),
  }),
];

export function tutorItem(
  overrides: Partial<TutorScheduleItem> = {},
): TutorScheduleItem {
  return {
    id: '00000000-0000-4000-8000-000000067e01',
    kind: 'individual',
    state: 'confirmed',
    starts_at: at(0, 10),
    ends_at: at(0, 11),
    subject: MATH,
    classroom: { id: '00000000-0000-4000-8000-000000067f01', name: 'Aula 2' },
    participants: [
      { student_id: LUCIA.student_id, display_name: LUCIA.display_name },
    ],
    ...overrides,
  };
}

export const TUTOR_ITEMS: TutorScheduleItem[] = [
  tutorItem(),
  tutorItem({
    id: '00000000-0000-4000-8000-000000067e02',
    state: 'offered',
    starts_at: at(1, 15),
    ends_at: at(1, 16),
    subject: PHYSICS,
    classroom: null,
  }),
  tutorItem({
    id: '00000000-0000-4000-8000-000000067e03',
    kind: 'group',
    state: 'completed',
    starts_at: at(2, 9),
    ends_at: at(2, 10),
    participants: [
      { student_id: LUCIA.student_id, display_name: LUCIA.display_name },
      { student_id: SELF.student_id, display_name: SELF.display_name },
    ],
  }),
];
