import type {
  BusinessSettings,
  OperatingHoursItem,
  SchedulingPolicy,
  SchedulingPolicyOverview,
} from './api';

// Settings bodies for tests and stories (#37): the migration's defaults.

export const DEFAULT_HOURS: OperatingHoursItem[] = [
  ...[1, 2, 3, 4, 5].map((weekday) => ({
    weekday,
    opens_at: '09:00',
    closes_at: '20:00',
  })),
  { weekday: 6, opens_at: '09:00', closes_at: '14:00' },
];

export const SETTINGS: BusinessSettings = {
  center_name: 'Classora',
  contact_phone: null,
  contact_email: null,
  address: null,
  time_zone: 'America/Mexico_City',
  operating_hours: DEFAULT_HOURS,
  version: 1,
  updated_at: '2026-10-19T12:00:00Z',
};

/** A center whose stored zone is not one of Mexico's. */
export const SETTINGS_ELSEWHERE: BusinessSettings = {
  ...SETTINGS,
  center_name: 'Centro Norte',
  contact_phone: '+52 55 1234 5678',
  contact_email: 'hola@centro.mx',
  address: 'Av. Reforma 1, CDMX',
  time_zone: 'America/Bogota',
  version: 4,
};

// Scheduling policy bodies (#61): version 1 of migration 0030, an upcoming
// version with a 12-hour cancellation notice and an earlier one.

export const POLICY_V1: SchedulingPolicy = {
  id: '01900000-0000-7000-8000-000000000001',
  effective_from: '2000-01-01T00:00:00Z',
  created_at: '2026-10-20T12:00:00Z',
  booking_notice_hours: 24,
  change_notice_hours: 24,
  response_hours: 48,
  makeup_after_on_time: true,
  makeup_after_late: false,
  makeup_after_no_show: false,
  makeup_window_days: 30,
};

export const POLICY_CURRENT: SchedulingPolicy = {
  ...POLICY_V1,
  id: '01900000-0000-7000-8000-000000000002',
  effective_from: '2026-09-01T06:00:00Z',
  created_at: '2026-08-20T12:00:00Z',
  response_hours: 24,
  makeup_after_late: true,
};

export const POLICY_UPCOMING: SchedulingPolicy = {
  ...POLICY_CURRENT,
  id: '01900000-0000-7000-8000-000000000003',
  effective_from: '2099-12-01T06:00:00Z',
  created_at: '2026-10-20T13:00:00Z',
  change_notice_hours: 12,
};

/** A fresh database: only version 1. */
export const OVERVIEW_V1: SchedulingPolicyOverview = {
  current: POLICY_V1,
  upcoming: [],
  history: [],
};

/** A current version, one upcoming change and version 1 in the history. */
export const OVERVIEW_WITH_UPCOMING: SchedulingPolicyOverview = {
  current: POLICY_CURRENT,
  upcoming: [POLICY_UPCOMING],
  history: [POLICY_V1],
};
