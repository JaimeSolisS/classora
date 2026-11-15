import type { AttendanceItem, SessionAttendance } from './api';

// Attendance data for tests and stories (#76 H7, H8): a started group
// session of three students, unmarked, partially marked, with a corrected
// row, not started, locked (completed) and not recordable.

export const ZONE = 'America/Mexico_City';
export const SESSION_ID = '00000000-0000-4000-8000-000000076a01';
export const ANA = '00000000-0000-4000-8000-000000076b01';
export const BRUNO = '00000000-0000-4000-8000-000000076b02';
export const CARLA = '00000000-0000-4000-8000-000000076b03';

function item(
  id: string,
  name: string,
  overrides: Partial<AttendanceItem> = {},
): AttendanceItem {
  return {
    student: { id, display_name: name },
    status: null,
    marked_by: null,
    marked_at: null,
    corrected: false,
    corrections: [],
    ...overrides,
  };
}

export function attendance(
  overrides: Partial<SessionAttendance> = {},
): SessionAttendance {
  return {
    class_session_id: SESSION_ID,
    version: 3,
    kind: 'group',
    state: 'confirmed',
    // Monday 2026-11-09, 10:00-11:00 in Mexico City.
    starts_at: '2026-11-09T16:00:00Z',
    ends_at: '2026-11-09T17:00:00Z',
    subject: {
      id: '00000000-0000-4000-8000-000000076c01',
      name: 'Matemáticas',
      education_level_name: 'Secundaria',
    },
    can_mark: true,
    can_correct: false,
    block: null,
    items: [
      item(ANA, 'Ana Díaz'),
      item(BRUNO, 'Bruno Gómez'),
      item(CARLA, 'Carla Ruiz'),
    ],
    ...overrides,
  };
}

const MARKED = {
  marked_by: { display_name: 'Carmen Ruiz' },
  marked_at: '2026-11-09T16:10:00Z',
};

export const UNMARKED = attendance();

export const PARTIAL = attendance({
  items: [
    item(ANA, 'Ana Díaz', { status: 'present', ...MARKED }),
    item(BRUNO, 'Bruno Gómez', { status: 'late', ...MARKED }),
    item(CARLA, 'Carla Ruiz'),
  ],
});

export const CORRECTED_ROW = attendance({
  items: [
    item(ANA, 'Ana Díaz', { status: 'present', ...MARKED }),
    item(BRUNO, 'Bruno Gómez', {
      status: 'absent',
      marked_by: { display_name: 'Laura Coordinadora' },
      marked_at: '2026-11-10T15:00:00Z',
      corrected: true,
      corrections: [
        {
          id: '00000000-0000-4000-8000-000000076d01',
          previous_status: 'late',
          new_status: 'absent',
          reason: 'Recepción confirmó que no llegó',
          corrected_by: { display_name: 'Laura Coordinadora' },
          created_at: '2026-11-10T15:00:00Z',
        },
      ],
    }),
    item(CARLA, 'Carla Ruiz', { status: 'late', ...MARKED }),
  ],
});

export const NOT_STARTED = attendance({
  can_mark: false,
  block: 'attendance_not_started',
});

export const LOCKED = attendance({
  ...CORRECTED_ROW,
  state: 'completed',
  can_mark: false,
  block: 'attendance_locked',
});

export const NOT_RECORDABLE = attendance({
  state: 'cancelled',
  can_mark: false,
  block: 'attendance_not_recordable',
});

/** The staff view of the corrected session: a correction is possible. */
export const STAFF_VIEW = attendance({
  ...CORRECTED_ROW,
  can_mark: false,
  can_correct: true,
});
