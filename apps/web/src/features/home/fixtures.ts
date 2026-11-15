import type { SessionUserBody } from '@/features/auth/session';
import type { CalendarItem, CalendarPage } from '@/features/calendar/api';
import { item } from '@/features/calendar/fixtures';
import {
  FAMILY_ID,
  OWN_FAMILY,
  SUMMARY,
} from '@/features/family-portal/billing/fixtures';
import type {
  FamilyScheduleItem,
  FamilyStudent,
  TutorScheduleItem,
} from '@/features/my-schedule/api';
import { familyItem, tutorItem } from '@/features/my-schedule/fixtures';
import { TEST_USER } from '@/test/api-stub';
import type { LocalDate } from '@/lib/time';

// Home fixtures (HF-06 F1, F3). The center's "today" is Wednesday
// 2031-04-02 in America/Mexico_City (UTC-6, no DST); "now" is 12:00 local.

export const TODAY = '2031-04-02' as LocalDate;
export const NOW = Date.parse('2031-04-02T18:00:00Z');

/** The instant of `hh:mm` local on TODAY + `days` (Mexico City, UTC-6). */
export function at(days: number, hh: number, mm = 0): string {
  return new Date(Date.UTC(2031, 3, 2 + days, hh + 6, mm)).toISOString();
}

const user = (
  username: string,
  displayName: string,
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({
  ...TEST_USER,
  username,
  display_name: displayName,
  roles,
  permissions,
});

export const OWNER = user(
  'demo.owner',
  'Demo Owner',
  ['owner'],
  [
    'finance.report.view',
    'schedule.view',
    'schedule.manage',
    'tutor.view',
    'billing.view',
    'teaching_hours.approve',
    'registration.review',
  ],
);
export const ADMIN = user(
  'demo.admin',
  'Demo Admin',
  ['administrator'],
  [
    'schedule.view',
    'schedule.manage',
    'tutor.view',
    'tutor.manage',
    'user.view',
  ],
);
export const COORDINATOR = user(
  'demo.coordinator',
  'Demo Coordinator',
  ['academic_coordinator'],
  ['schedule.view', 'tutor.view', 'teaching_hours.approve'],
);
export const FINANCE = user(
  'demo.finance',
  'Demo Finance',
  ['finance_staff'],
  ['finance.report.view', 'schedule.view', 'billing.view', 'expense.view'],
);
export const TUTOR = user(
  'demo.tutor',
  'Demo Tutor',
  ['tutor'],
  ['student.view', 'tutor.view'],
);
export const STUDENT = user(
  'demo.student',
  'Demo Student',
  ['student'],
  ['student.view', 'consent.record'],
);
export const GUARDIAN = user(
  'demo.guardian',
  'Demo Guardian',
  ['guardian'],
  ['student.view', 'guardian.view', 'consent.record'],
);
export const NO_ROLE = user('no.role', 'Nadie Aún', [], []);
export const COORDINATOR_TUTOR = user(
  'demo.coordinator',
  'Demo Coordinator',
  ['academic_coordinator', 'tutor'],
  ['schedule.view', 'tutor.view', 'teaching_hours.approve', 'student.view'],
);

// ----------------------------------------------------------- staff (B1)

export function staffItem(overrides: Partial<CalendarItem> = {}): CalendarItem {
  return item({ starts_at: at(0, 9), ends_at: at(0, 10), ...overrides });
}

export function calendarPage(
  items: CalendarItem[],
  truncated = false,
): CalendarPage {
  return { items, truncated };
}

/** The class types by row (HF-07): the first three rows are one of each. */
const KINDS = ['individual', 'group', 'course'] as const;

/** A busy center: 10 classes today, one without a tutor, one in conflict. */
export const BUSY_TODAY: CalendarItem[] = [
  ...Array.from({ length: 8 }, (_, index) =>
    staffItem({
      kind: KINDS[index % 3]!,
      starts_at: at(0, 8 + index),
      ends_at: at(0, 9 + index),
    }),
  ),
  staffItem({
    state: 'unassigned',
    tutor: null,
    starts_at: at(0, 17),
    ends_at: at(0, 18),
  }),
  staffItem({
    state: 'conflict',
    starts_at: at(0, 18),
    ends_at: at(0, 19),
  }),
  // Tomorrow: not today; it still counts for "needs attention".
  staffItem({
    state: 'unassigned',
    tutor: null,
    starts_at: at(1, 10),
    ends_at: at(1, 11),
  }),
];

// ---------------------------------------------------- tutor (B2) and family

export const TUTOR_TODAY: TutorScheduleItem[] = [
  // Ended this morning, still confirmed: to close out.
  tutorItem({
    id: '00000000-0000-4000-8000-000000549a01',
    starts_at: at(0, 9),
    ends_at: at(0, 10),
  }),
  tutorItem({
    id: '00000000-0000-4000-8000-000000549a02',
    kind: 'group',
    state: 'offered',
    starts_at: at(0, 16),
    ends_at: at(0, 17),
    classroom: null,
  }),
  tutorItem({
    id: '00000000-0000-4000-8000-000000549a03',
    kind: 'course',
    starts_at: at(2, 16),
    ends_at: at(2, 17),
  }),
  tutorItem({
    id: '00000000-0000-4000-8000-000000549a04',
    state: 'cancelled',
    starts_at: at(3, 16),
    ends_at: at(3, 17),
  }),
];

export const KID_A: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000549c01',
  display_name: 'Lucía Pérez',
  relation: 'guardian',
  is_minor: true,
  schedule_access: 'allowed',
};
export const KID_B: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000549c02',
  display_name: 'Mateo Pérez',
  relation: 'guardian',
  is_minor: false,
  schedule_access: 'consent_required',
};
export const SELF_STUDENT: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000549c03',
  display_name: 'Demo Student',
  relation: 'self',
  is_minor: false,
  schedule_access: 'allowed',
};

export const FAMILY_TODAY: FamilyScheduleItem[] = [
  familyItem({
    id: '00000000-0000-4000-8000-000000549d01',
    starts_at: at(0, 17),
    ends_at: at(0, 18),
  }),
  familyItem({
    id: '00000000-0000-4000-8000-000000549d02',
    kind: 'group',
    starts_at: at(4, 10),
    ends_at: at(4, 11),
  }),
];

/** An upcoming course class (HF-07: the family's third class type). */
export const FAMILY_COURSE: FamilyScheduleItem = familyItem({
  id: '00000000-0000-4000-8000-000000549d03',
  kind: 'course',
  starts_at: at(5, 16),
  ends_at: at(5, 17),
});

export const scheduleOf = <T>(items: T[]) => ({ items, truncated: false });

// ---------------------------------------------------------- pending (C1)

export const offsetPage = (total: number) => ({
  items: [],
  total,
  limit: 1,
  offset: 0,
});

export const overdueAccountsPage = (total: number) => ({
  ...offsetPage(total),
  today: TODAY,
  totals: {
    accounts: total,
    amount: '0.00',
    days_1_30: '0.00',
    days_31_60: '0.00',
    days_61_90: '0.00',
    days_over_90: '0.00',
  },
});

export function assignmentsPage(submitted: number[], more = false) {
  return {
    items: submitted.map((count, index) => ({
      id: `00000000-0000-4000-8000-${String(549000 + index).padStart(12, '0')}`,
      counts: {
        recipients: count + 1,
        pending: 0,
        missing: 1,
        submitted: count,
        late: 0,
        graded: 0,
      },
    })),
    next_cursor: more ? 'next' : null,
  };
}

export const homeworkSummary = (pending: number, missing: number) => ({
  items: [],
  summary: { pending, missing, submitted: 0, graded: 0 },
  next_cursor: null,
});

export { FAMILY_ID, OWN_FAMILY, SUMMARY };
