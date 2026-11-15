import type { ImportJob, ImportJobPage, ImportRow, ImportRowPage } from './api';

// Synthetic imports for the tests and stories (M8-05b). No real names.

export const JOB_ID = '0199b000-0000-7000-8000-000000000001';
export const TUTORS_JOB_ID = '0199b000-0000-7000-8000-000000000002';
export const STUDENT_ID = '0199b000-0000-7000-8000-0000000000a1';
export const TUTOR_USER_ID = '0199b000-0000-7000-8000-0000000000b1';

const ADMIN = {
  id: '00000000-0000-4000-8000-000000000001',
  display_name: 'Demo Owner',
};

export const STAGED: ImportJob = {
  id: JOB_ID,
  kind: 'students',
  status: 'staged',
  file_name: 'alumnos.csv',
  ignored_columns: ['notas'],
  counts: {
    total: 4,
    ready: 2,
    duplicate: 1,
    error: 1,
    created: 0,
    skipped: 0,
    failed: 0,
  },
  created_by: ADMIN,
  created_at: '2026-11-05T16:00:00Z',
  committed_by: null,
  committed_at: null,
  // 7 days later, 10:00 in Mexico City: Nov 12, 2026.
  expires_at: '2026-11-12T16:00:00Z',
  version: 1,
};

export const COMMITTING: ImportJob = {
  ...STAGED,
  status: 'committing',
  committed_by: ADMIN,
  committed_at: '2026-11-05T16:05:00Z',
  expires_at: null,
  version: 2,
};

export const COMMITTED: ImportJob = {
  ...COMMITTING,
  status: 'committed',
  counts: {
    total: 4,
    ready: 0,
    duplicate: 1,
    error: 1,
    created: 2,
    skipped: 0,
    failed: 0,
  },
  version: 3,
};

export const FAILED: ImportJob = {
  ...COMMITTING,
  status: 'failed',
  counts: {
    total: 4,
    ready: 1,
    duplicate: 1,
    error: 1,
    created: 1,
    skipped: 0,
    failed: 0,
  },
  expires_at: '2026-11-12T16:10:00Z',
  version: 3,
};

export const DISCARDED: ImportJob = {
  ...STAGED,
  status: 'discarded',
  expires_at: null,
  version: 2,
};

export const TUTORS_STAGED: ImportJob = {
  ...STAGED,
  id: TUTORS_JOB_ID,
  kind: 'tutors',
  file_name: 'tutores.csv',
  ignored_columns: [],
  counts: {
    total: 1,
    ready: 1,
    duplicate: 0,
    error: 0,
    created: 0,
    skipped: 0,
    failed: 0,
  },
};

const VALUES = {
  given_names: 'Ana Sofía',
  family_names: 'Ejemplo Pérez',
  birthdate: '2014-03-15',
  email: '',
  contact_name: 'María Ejemplo',
  contact_relationship: 'Madre',
  contact_phone: '55 0000 0000',
};

export const STAGED_ROWS: ImportRow[] = [
  {
    row_number: 2,
    status: 'ready',
    values: VALUES,
    errors: [],
    warnings: [],
    record_id: null,
  },
  {
    row_number: 3,
    status: 'duplicate',
    values: { ...VALUES, given_names: 'ana sofia' },
    errors: [],
    warnings: [{ column: null, code: 'duplicate_in_file' }],
    record_id: null,
  },
  {
    row_number: 4,
    status: 'error',
    values: { ...VALUES, birthdate: '31/02/2014', email: 'no-es-correo' },
    errors: [
      { column: 'birthdate', code: 'invalid_date' },
      { column: 'email', code: 'invalid_email' },
      { column: 'phone', code: 'brand_new_code' },
    ],
    warnings: [],
    record_id: null,
  },
  {
    row_number: 6,
    status: 'ready',
    values: { ...VALUES, given_names: 'Luis' },
    errors: [],
    warnings: [],
    record_id: null,
  },
];

export const COMMITTED_ROWS: ImportRow[] = [
  {
    row_number: 2,
    status: 'created',
    values: null,
    errors: [],
    warnings: [],
    record_id: STUDENT_ID,
  },
  {
    row_number: 3,
    status: 'duplicate',
    values: null,
    errors: [],
    warnings: [{ column: null, code: 'duplicate_in_file' }],
    record_id: null,
  },
  {
    row_number: 4,
    status: 'error',
    values: null,
    errors: [{ column: 'birthdate', code: 'invalid_date' }],
    warnings: [],
    record_id: null,
  },
  {
    row_number: 6,
    status: 'created',
    values: null,
    errors: [],
    warnings: [],
    record_id: '0199b000-0000-7000-8000-0000000000a2',
  },
];

export const TUTOR_ROWS: ImportRow[] = [
  {
    row_number: 2,
    status: 'created',
    values: null,
    errors: [],
    warnings: [],
    record_id: TUTOR_USER_ID,
  },
];

export function jobPage(
  items: ImportJob[],
  total = items.length,
): ImportJobPage {
  return { items, total, limit: 25, offset: 0 };
}

export function rowPage(
  items: ImportRow[],
  total = items.length,
): ImportRowPage {
  return { items, total, limit: 50, offset: 0 };
}
