import type { KnownDataset } from './ExportForm';
import type { ExportJob, ExportJobPage } from './api';
import type { ReportSetup } from './setups';

// Stories and tests only: the #124 dataset catalogue (labels as the API
// returns them for an English-speaking caller) and one export per status.

const column = (
  code: string,
  label: string,
  kind: KnownDataset['columns'][number]['kind'],
  groupable: boolean,
) => ({ code, label, kind, groupable });

export const STUDENTS_DATASET: KnownDataset = {
  code: 'students',
  label: 'Students',
  personal: true,
  date_filter: 'none',
  columns: [
    column('family_names', 'Family names', 'text', false),
    column('given_names', 'Given names', 'text', false),
    column('is_active', 'Active', 'boolean', true),
    column('is_minor', 'Minor', 'boolean', true),
    column('school_grade', 'School grade', 'text', true),
    column('education_level', 'Education level', 'text', true),
  ],
};

export const SESSIONS_DATASET: KnownDataset = {
  code: 'class_sessions',
  label: 'Classes',
  personal: true,
  date_filter: 'required',
  columns: [
    column('date', 'Date', 'date', true),
    column('start_time', 'Start', 'time', false),
    column('end_time', 'End', 'time', false),
    column('duration_minutes', 'Duration (minutes)', 'integer', false),
    column('kind', 'Class type', 'enum', true),
    column('state', 'Status', 'enum', true),
    column('subject', 'Subject', 'text', true),
    column('education_level', 'Education level', 'text', true),
    column('tutor', 'Tutor', 'text', true),
    column('classroom', 'Classroom', 'text', true),
    column('students', 'Students', 'list', false),
    column('series', 'Weekly series', 'boolean', false),
  ],
};

export const EXPENSES_DATASET: KnownDataset = {
  code: 'expenses',
  label: 'Expenses',
  personal: false,
  date_filter: 'optional',
  columns: [
    column('incurred_on', 'Expense date', 'date', true),
    column('due_on', 'Due date', 'date', true),
    column('category', 'Category', 'text', true),
    column('vendor', 'Vendor', 'text', true),
    column('description', 'Description', 'text', false),
    column('amount', 'Amount', 'money', false),
    column('adjusted_amount', 'Net amount', 'money', false),
    column('paid_amount', 'Paid amount', 'money', false),
    column('open_amount', 'Open amount', 'money', false),
    column('status', 'Status', 'enum', true),
    column('receipt_reference', 'Receipt reference', 'text', false),
    column('template', 'Recurring expense', 'text', true),
  ],
};

export const ALL_DATASETS = [
  STUDENTS_DATASET,
  SESSIONS_DATASET,
  EXPENSES_DATASET,
] as const;

/** The pinned "now" of the stories and the history tests. */
export const NOW = new Date('2026-11-05T18:00:00Z');

const base = {
  format: 'csv',
  language: 'en',
  created_at: '2026-11-05T17:00:00Z',
} as const;

export const QUEUED: ExportJob = {
  ...base,
  id: '00000000-0000-4000-8000-0000000000e1',
  dataset: 'expenses',
  status: 'queued',
  parameters: { dataset: 'expenses', format: 'csv', filters: {} },
  row_count: null,
  byte_size: null,
  file_name: 'classora-expenses-20261105-1100.csv',
  error_code: null,
  finished_at: null,
  expires_at: null,
};

export const READY: ExportJob = {
  ...QUEUED,
  id: '00000000-0000-4000-8000-0000000000e2',
  status: 'ready',
  row_count: 1234,
  byte_size: 48_300,
  finished_at: '2026-11-05T17:00:05Z',
  // 5 hours after NOW.
  expires_at: '2026-11-05T23:00:00Z',
};

export const READY_XLSX: ExportJob = {
  ...READY,
  id: '00000000-0000-4000-8000-0000000000e3',
  dataset: 'students',
  format: 'xlsx',
  parameters: {
    dataset: 'students',
    format: 'xlsx',
    filters: { status: 'active' },
  },
  file_name: 'classora-students-20261105-1030.xlsx',
  // 20 minutes after NOW.
  expires_at: '2026-11-05T18:20:00Z',
};

export const FAILED: ExportJob = {
  ...QUEUED,
  id: '00000000-0000-4000-8000-0000000000e4',
  dataset: 'class_sessions',
  status: 'failed',
  parameters: {
    dataset: 'class_sessions',
    format: 'csv',
    filters: { from: '2026-01-01', to: '2026-12-31', include_cancelled: false },
  },
  error_code: 'row_limit_exceeded',
  finished_at: '2026-11-05T17:00:09Z',
};

export const EXPIRED: ExportJob = {
  ...READY,
  id: '00000000-0000-4000-8000-0000000000e5',
  status: 'expired',
  created_at: '2026-11-03T15:00:00Z',
  finished_at: '2026-11-03T15:00:04Z',
  expires_at: '2026-11-04T15:00:04Z',
};

/** Still `ready`, but past its expiry (the purge hasn't run yet). */
export const READY_PAST_EXPIRY: ExportJob = {
  ...READY,
  id: '00000000-0000-4000-8000-0000000000e6',
  expires_at: '2026-11-05T17:59:00Z',
};

export function jobPage(
  items: ExportJob[],
  total = items.length,
): ExportJobPage {
  return { items, total, limit: 20, offset: 0 };
}

// M8-08: saved setups (the caller is the Owner `TEST_USER` in the tests).
export const SETUP_OWN: ReportSetup = {
  id: '00000000-0000-4000-8000-0000000000a1',
  name: 'Monthly expenses',
  dataset: 'expenses',
  parameters: {
    dataset: 'expenses',
    format: 'xlsx',
    columns: ['vendor', 'amount', 'incurred_on', 'status'],
    group_by: 'vendor',
    filters: {
      status: ['unpaid', 'paid'],
      incurred_from: '2026-10-01',
      incurred_to: '2026-10-31',
    },
  },
  shared_role: 'finance_staff',
  created_by: { id: 'user-owner', display_name: 'Olga Owner' },
  mine: true,
  can_run: true,
  version: 3,
  created_at: '2026-11-01T17:00:00Z',
  updated_at: '2026-11-02T17:00:00Z',
};

export const SETUP_SHARED: ReportSetup = {
  id: '00000000-0000-4000-8000-0000000000a2',
  name: 'Active students',
  dataset: 'students',
  parameters: {
    dataset: 'students',
    format: 'csv',
    filters: { status: 'active' },
  },
  shared_role: 'owner',
  created_by: { id: 'user-coordinator', display_name: 'Carla Coordinator' },
  mine: false,
  can_run: true,
  version: 1,
  created_at: '2026-11-01T17:00:00Z',
  updated_at: '2026-11-01T17:00:00Z',
};

export const SETUP_NOT_RUNNABLE: ReportSetup = {
  ...SETUP_OWN,
  id: '00000000-0000-4000-8000-0000000000a3',
  name: 'Old sessions',
  dataset: 'class_sessions',
  parameters: {
    dataset: 'class_sessions',
    format: 'csv',
    filters: { from: '2026-09-01', to: '2026-09-30', include_cancelled: false },
  },
  shared_role: null,
  can_run: false,
  version: 1,
};
