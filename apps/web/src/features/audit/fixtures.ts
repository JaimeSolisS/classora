import type { AuditEvent } from './api';

// Audit events for the tests and stories (#120): an assignment update with
// both names, a masked payroll create, a delete, a named event without a
// record and actor, an unknown actor, and nested JSON.

export const OWNER = {
  id: '00000000-0000-4000-8000-0000000000a1',
  display_name: 'Ana Owner',
};
export const LAURA = {
  id: '00000000-0000-4000-8000-0000000000d7',
  display_name: 'Laura Méndez',
};
export const TUTOR = {
  id: '00000000-0000-4000-8000-0000000000b2',
  display_name: 'Tomás Tutor',
};

export const APPROVAL_ID = '0199a000-0000-7000-8000-00000000c001';
export const RATE_ID = '0199a000-0000-7000-8000-00000000c002';

const base = {
  details: null,
  request_id: null,
  accounts: [],
} satisfies Partial<AuditEvent>;

/** #118: an assignment (old and new assignee named). */
export const ASSIGNMENT: AuditEvent = {
  ...base,
  id: '0199a000-0000-7000-8000-0000000e0001',
  occurred_at: '2026-11-04T18:30:05Z',
  actor_id: OWNER.id,
  actor: OWNER,
  action: 'update',
  record_type: 'approval_requests',
  record_id: APPROVAL_ID,
  changes: {
    assignee_user_id: { old: LAURA.id, new: TUTOR.id },
  },
  accounts: [LAURA, TUTOR].sort((a, b) => (a.id < b.id ? -1 : 1)),
  request_id: 'req-assign-1',
};

/** A masked compensation rate create (A2). */
export const MASKED_RATE: AuditEvent = {
  ...base,
  id: '0199a000-0000-7000-8000-0000000e0002',
  occurred_at: '2026-11-04T17:00:00Z',
  actor_id: OWNER.id,
  actor: OWNER,
  action: 'create',
  record_type: 'compensation_rates',
  record_id: RATE_ID,
  changes: {
    tutor_user_id: { old: null, new: TUTOR.id },
    rate: { old: '[masked]', new: '[masked]' },
    rate_model: { old: null, new: 'per_hour' },
  },
  accounts: [TUTOR],
};

/** A delete, with nested JSON. */
export const DELETED: AuditEvent = {
  ...base,
  id: '0199a000-0000-7000-8000-0000000e0003',
  occurred_at: '2026-11-03T15:00:00Z',
  actor_id: LAURA.id,
  actor: LAURA,
  action: 'delete',
  record_type: 'weekly_windows',
  record_id: '0199a000-0000-7000-8000-00000000c003',
  changes: {
    windows: { old: [{ weekday: 1, start: '09:00' }], new: null },
    label: { old: 'Mañanas', new: null },
  },
};

/** A seed or job event: no actor ("System"). */
export const SYSTEM: AuditEvent = {
  ...base,
  id: '0199a000-0000-7000-8000-0000000e0004',
  occurred_at: '2026-11-02T06:00:00Z',
  actor_id: null,
  actor: null,
  action: 'create',
  record_type: 'education_levels',
  record_id: '0199a000-0000-7000-8000-00000000c004',
  changes: { name: { old: null, new: 'Primaria' } },
};

/** A named security event without a record or an actor. */
export const LOGIN_FAILED: AuditEvent = {
  ...base,
  id: '0199a000-0000-7000-8000-0000000e0005',
  occurred_at: '2026-11-01T12:00:00Z',
  actor_id: null,
  actor: null,
  action: 'identity.login_failed',
  record_type: null,
  record_id: null,
  changes: null,
  details: { reason: 'invalid_credentials', attempts: 3 },
};

/** An actor id with no name (the resolver did not know it). */
export const UNKNOWN_ACTOR: AuditEvent = {
  ...base,
  id: '0199a000-0000-7000-8000-0000000e0006',
  occurred_at: '2026-10-31T12:00:00Z',
  actor_id: '0199a000-0000-7000-8000-00000000dead',
  actor: null,
  action: 'soft_delete',
  record_type: 'students',
  record_id: '0199a000-0000-7000-8000-00000000c006',
  changes: {
    deleted_at: { old: null, new: '2026-10-31T12:00:00+00:00' },
    created_by_user_id: { old: TUTOR.id, new: TUTOR.id },
  },
};

export const MIXED_EVENTS: AuditEvent[] = [
  ASSIGNMENT,
  MASKED_RATE,
  DELETED,
  SYSTEM,
  LOGIN_FAILED,
  UNKNOWN_ACTOR,
];

export const RECORD_TYPES = [
  'approval_requests',
  'compensation_rates',
  'education_levels',
  'students',
  'users',
  'weekly_windows',
];

/** `count` update events by OWNER, newest first, one second apart. */
export function manyEvents(count: number, offset = 0): AuditEvent[] {
  return Array.from({ length: count }, (_, index) => {
    const n = offset + index;
    const seconds = String(59 - (n % 60)).padStart(2, '0');
    return {
      ...ASSIGNMENT,
      id: `0199a000-0000-7000-8000-${String(1000 + n).padStart(12, '0')}`,
      occurred_at: `2026-11-04T10:${String(59 - Math.floor(n / 60)).padStart(2, '0')}:${seconds}Z`,
    };
  });
}
