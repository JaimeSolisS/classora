import type { BusinessSettings, OperatingHoursItem } from './api';

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
