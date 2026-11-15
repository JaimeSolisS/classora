import type { AvailableSlots } from '@/features/family-portal/requests/api';
import type { FamilyStudent, ScheduleEntry } from '@/features/my-schedule/api';
import { familyItem, LUCIA, tutorItem } from '@/features/my-schedule/fixtures';
import type {
  ChangeRequestDetail,
  ChangeRequestItem,
  SchedulingPolicy,
} from './api';

// Sample data for the change request tests and stories (#62b D3, D4). The
// sessions are the #67 fixtures: Monday 2026-11-09 10:00-11:00 in
// America/Mexico_City (UTC-6, no DST) = 16:00Z.

export { ZONE } from '@/features/my-schedule/fixtures';

export const SESSION_STARTS = '2026-11-09T16:00:00Z';
const HOUR = 3_600_000;
/** 25 hours before the session (an on-time family request). */
export const EARLY = Date.parse(SESSION_STARTS) - 25 * HOUR;
/** 3 hours before the session (a late family request). */
export const LATE = Date.parse(SESSION_STARTS) - 3 * HOUR;

/** The #61 default-like policy, as in the API's policy unit tests. */
export const POLICY: SchedulingPolicy = {
  id: '00000000-0000-4000-8000-000000247a01',
  effective_from: '2000-01-01T00:00:00Z',
  created_at: '2000-01-01T00:00:00Z',
  booking_notice_hours: 24,
  change_notice_hours: 24,
  response_hours: 48,
  makeup_after_on_time: true,
  makeup_after_late: false,
  makeup_after_no_show: false,
  makeup_window_days: 30,
};

export const FAMILY_ENTRY: ScheduleEntry = {
  audience: 'family',
  item: familyItem(),
};
export const TUTOR_ENTRY: ScheduleEntry = {
  audience: 'tutor',
  item: tutorItem(),
};

export const SECOND_CHILD: FamilyStudent = {
  student_id: '00000000-0000-4000-8000-000000247c02',
  display_name: 'Tomás Pérez',
  relation: 'guardian',
  is_minor: true,
  schedule_access: 'allowed',
};

/** Two days of slots from Monday 2026-11-09 (the page starts on today). */
export function slotsOf(from: string, to: string): AvailableSlots {
  return {
    time_zone: 'America/Mexico_City',
    student_id: LUCIA.student_id,
    subject_id: FAMILY_ENTRY.item.subject.id,
    duration_minutes: 60,
    from,
    to,
    days: [
      {
        date: '2026-11-10',
        slots: [
          {
            starts_at: '2026-11-10T18:00:00Z',
            ends_at: '2026-11-10T19:00:00Z',
            already_requested: false,
          },
          {
            starts_at: '2026-11-10T19:00:00Z',
            ends_at: '2026-11-10T20:00:00Z',
            already_requested: false,
          },
        ],
      },
      { date: '2026-11-11', slots: [] },
    ],
  };
}

const SUBJECT = {
  id: FAMILY_ENTRY.item.subject.id,
  name: 'Matemáticas',
  education_level_name: 'Secundaria',
};

export function changeRequest(
  overrides: Partial<ChangeRequestItem> = {},
): ChangeRequestItem {
  return {
    id: '00000000-0000-4000-8000-000000247d01',
    kind: 'cancellation',
    status: 'pending',
    class_session: {
      id: FAMILY_ENTRY.item.id,
      starts_at: SESSION_STARTS,
      ends_at: '2026-11-09T17:00:00Z',
      subject: SUBJECT,
    },
    student: { id: LUCIA.student_id, display_name: LUCIA.display_name },
    proposed_starts_at: null,
    proposed_ends_at: null,
    note: null,
    outcome: {
      classification: 'on_time',
      makeup_allowed: true,
      makeup_until: '2026-12-09T16:00:00Z',
    },
    decision: null,
    requested_by_me: true,
    can_withdraw: true,
    created_at: '2026-11-08T10:00:00Z',
    resolved_at: null,
    version: 1,
    ...overrides,
  };
}

export const PENDING_CANCELLATION = changeRequest({
  note: 'Tiene consulta médica.',
});

export const PENDING_RESCHEDULE = changeRequest({
  id: '00000000-0000-4000-8000-000000247d02',
  kind: 'reschedule',
  proposed_starts_at: '2026-11-10T18:00:00Z',
  proposed_ends_at: '2026-11-10T19:00:00Z',
  outcome: {
    classification: 'late',
    makeup_allowed: false,
    makeup_until: null,
  },
});

export const APPROVED_EXCEPTION = changeRequest({
  id: '00000000-0000-4000-8000-000000247d03',
  status: 'approved',
  outcome: {
    classification: 'excused',
    makeup_allowed: true,
    makeup_until: '2026-12-09T16:00:00Z',
  },
  decision: {
    exception: true,
    reason: 'Hubo una emergencia familiar.',
    decided_at: '2026-11-08T12:00:00Z',
  },
  can_withdraw: false,
  resolved_at: '2026-11-08T12:00:00Z',
  version: 2,
});

export const APPROVED = changeRequest({
  id: '00000000-0000-4000-8000-000000247d04',
  status: 'approved',
  decision: {
    exception: false,
    reason: null,
    decided_at: '2026-11-08T12:00:00Z',
  },
  can_withdraw: false,
  resolved_at: '2026-11-08T12:00:00Z',
  version: 2,
});

export const REJECTED = changeRequest({
  id: '00000000-0000-4000-8000-000000247d05',
  kind: 'reschedule',
  status: 'rejected',
  student: {
    id: SECOND_CHILD.student_id,
    display_name: SECOND_CHILD.display_name,
  },
  proposed_starts_at: '2026-11-10T18:00:00Z',
  proposed_ends_at: '2026-11-10T19:00:00Z',
  outcome: {
    classification: 'late',
    makeup_allowed: false,
    makeup_until: null,
  },
  decision: {
    exception: false,
    reason: 'No hay aula a esa hora.',
    decided_at: '2026-11-08T12:00:00Z',
  },
  can_withdraw: false,
  resolved_at: '2026-11-08T12:00:00Z',
  version: 2,
});

export const WITHDRAWN = changeRequest({
  id: '00000000-0000-4000-8000-000000247d06',
  status: 'withdrawn',
  can_withdraw: false,
  resolved_at: '2026-11-08T11:00:00Z',
  version: 2,
});

/** One request per status (and both approval kinds). */
export const ALL_REQUESTS: ChangeRequestItem[] = [
  PENDING_CANCELLATION,
  PENDING_RESCHEDULE,
  APPROVED_EXCEPTION,
  APPROVED,
  REJECTED,
  WITHDRAWN,
];

export function created(
  overrides: Partial<ChangeRequestDetail> = {},
): ChangeRequestDetail {
  return changeRequest(overrides);
}
