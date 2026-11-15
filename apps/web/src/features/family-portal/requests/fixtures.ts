import type { SessionUserBody } from '@/features/auth/session';
import { addDays } from '@/lib/time';
import type {
  AvailableSlots,
  SessionRequestItem,
  StudentChoice,
  SubjectChoice,
} from './api';

// Fixed data for the family request tests and stories (#59 F5). The center
// zone is America/Cancun (UTC-5 all year), which is neither the test host's
// zone nor the default center zone; the Tijuana week crosses the 2031
// spring-forward change (Sunday 2031-03-09).

export const CANCUN = 'America/Cancun';
export const TIJUANA = 'America/Tijuana';
/** "Now" for stories: Monday 2031-01-06 08:00 in Cancun. */
export const TODAY = '2031-01-06';

export const GUARDIAN_USER: SessionUserBody = {
  id: '00000000-0000-4000-8000-000000000059',
  username: 'laura.madre',
  display_name: 'Laura Madre',
  roles: ['guardian'],
  permissions: ['student.view', 'guardian.view'],
  csrf_token: 'csrf-token-ccccccccccccccccccccccccccccccccccc',
  privacy_notice_required: false,
  preferences: { language: null, theme: null },
  center_time_zone: CANCUN,
};

export const STUDENT_USER: SessionUserBody = {
  ...GUARDIAN_USER,
  id: '00000000-0000-4000-8000-000000000060',
  username: 'ana.alumna',
  display_name: 'Ana Alumna',
  roles: ['student'],
  permissions: ['student.view'],
};

export const STUDENT_ANA: StudentChoice = {
  id: '01a00000-0000-7000-8000-000000000001',
  name: 'Ana López',
};

export const STUDENT_LUIS: StudentChoice = {
  id: '01a00000-0000-7000-8000-000000000002',
  name: 'Luis López',
};

export const SUBJECTS: SubjectChoice[] = [
  {
    id: '01b00000-0000-7000-8000-000000000001',
    name: 'Física',
    levelName: 'Secundaria',
  },
  {
    id: '01b00000-0000-7000-8000-000000000002',
    name: 'Álgebra',
    levelName: 'Secundaria',
  },
  {
    id: '01b00000-0000-7000-8000-000000000003',
    name: 'Lectura',
    levelName: 'Primaria',
  },
];

export const PHYSICS = SUBJECTS[0]!;

/** The API's student list item for a choice. */
export function studentItem(choice: StudentChoice) {
  const [given, family] = choice.name.split(' ');
  return {
    id: choice.id,
    given_names: given,
    family_names: family,
    is_active: true,
    education_level: null,
    birthdate: null,
    created_at: '2030-01-01T00:00:00Z',
    updated_at: '2030-01-01T00:00:00Z',
    version: 1,
  };
}

/** The API's subject list item for a choice. */
export function subjectItem(choice: SubjectChoice) {
  return {
    id: choice.id,
    name: choice.name,
    education_level_id: '01c00000-0000-7000-8000-000000000001',
    education_level_name: choice.levelName,
    is_active: true,
    version: 1,
  };
}

function days(from: string, count: number): string[] {
  return Array.from({ length: count }, (_, n) => addDays(from, n));
}

/** A week in Cancun: Monday has 10:00, 10:30 (already requested) and
 * 11:00 for 60 minutes; the other days have none. */
export const WEEK_SLOTS: AvailableSlots = {
  time_zone: CANCUN,
  student_id: STUDENT_ANA.id,
  subject_id: PHYSICS.id,
  duration_minutes: 60,
  from: TODAY,
  to: '2031-01-12',
  days: days(TODAY, 7).map((date) => ({
    date,
    slots:
      date === TODAY
        ? [
            {
              starts_at: '2031-01-06T15:00:00Z',
              ends_at: '2031-01-06T16:00:00Z',
              already_requested: false,
            },
            {
              starts_at: '2031-01-06T15:30:00Z',
              ends_at: '2031-01-06T16:30:00Z',
              already_requested: true,
            },
            {
              starts_at: '2031-01-06T16:00:00Z',
              ends_at: '2031-01-06T17:00:00Z',
              already_requested: false,
            },
          ]
        : [],
  })),
};

/** A Tijuana week across the spring-forward change: 10:00 on Friday
 * (UTC-8) and on Monday (UTC-7). */
export const TIJUANA_SLOTS: AvailableSlots = {
  time_zone: TIJUANA,
  student_id: STUDENT_ANA.id,
  subject_id: PHYSICS.id,
  duration_minutes: 60,
  from: '2031-03-07',
  to: '2031-03-13',
  days: days('2031-03-07', 7).map((date) => ({
    date,
    slots:
      date === '2031-03-07'
        ? [
            {
              starts_at: '2031-03-07T18:00:00Z',
              ends_at: '2031-03-07T19:00:00Z',
              already_requested: false,
            },
          ]
        : date === '2031-03-10'
          ? [
              {
                starts_at: '2031-03-10T17:00:00Z',
                ends_at: '2031-03-10T18:00:00Z',
                already_requested: false,
              },
            ]
          : [],
  })),
};

const BASE_REQUEST: SessionRequestItem = {
  id: '01d00000-0000-7000-8000-000000000001',
  student: { id: STUDENT_ANA.id, display_name: STUDENT_ANA.name },
  subject: {
    id: PHYSICS.id,
    name: PHYSICS.name,
    education_level_name: PHYSICS.levelName,
  },
  starts_at: '2031-01-06T15:00:00Z',
  ends_at: '2031-01-06T16:00:00Z',
  duration_minutes: 60,
  status: 'pending',
  note: 'Prefiere por la mañana',
  requested_by_me: true,
  can_withdraw: true,
  created_at: '2031-01-05T12:00:00Z',
  resolved_at: null,
  version: 1,
  rejection_reason: null,
};

export const PENDING_REQUEST = BASE_REQUEST;

export const WITHDRAWN_REQUEST: SessionRequestItem = {
  ...BASE_REQUEST,
  id: '01d00000-0000-7000-8000-000000000002',
  starts_at: '2031-01-07T15:00:00Z',
  ends_at: '2031-01-07T15:45:00Z',
  duration_minutes: 45,
  status: 'withdrawn',
  note: null,
  can_withdraw: false,
  resolved_at: '2031-01-05T13:00:00Z',
  version: 2,
};

export const CONFIRMED_REQUEST: SessionRequestItem = {
  ...BASE_REQUEST,
  id: '01d00000-0000-7000-8000-000000000003',
  student: { id: STUDENT_LUIS.id, display_name: STUDENT_LUIS.name },
  starts_at: '2031-01-08T16:00:00Z',
  ends_at: '2031-01-08T17:30:00Z',
  duration_minutes: 90,
  status: 'confirmed',
  note: null,
  can_withdraw: false,
  resolved_at: '2031-01-05T14:00:00Z',
};

export const REJECTED_REQUEST: SessionRequestItem = {
  ...BASE_REQUEST,
  id: '01d00000-0000-7000-8000-000000000004',
  starts_at: '2031-01-09T15:00:00Z',
  ends_at: '2031-01-09T15:30:00Z',
  duration_minutes: 30,
  status: 'rejected',
  note: null,
  requested_by_me: false,
  can_withdraw: false,
  resolved_at: '2031-01-05T15:00:00Z',
  rejection_reason: 'No hay tutores disponibles ese día.',
};
