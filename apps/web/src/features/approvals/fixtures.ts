import type { ApprovalRequestItem } from './api';

// Approval items for stories (#40 F6): one per status, two types.

export const PENDING_REGISTRATION: ApprovalRequestItem = {
  id: '00000000-0000-4000-8000-0000000000c1',
  request_type: 'identity.registration',
  status: 'pending',
  priority: 'normal',
  requester: {
    id: '00000000-0000-4000-8000-0000000000a1',
    display_name: 'Rosa Díaz',
  },
  assignee: null,
  created_at: '2026-10-19T15:00:00Z',
  resolved_at: null,
  resolved_by: null,
  reason: null,
  subject: {
    username: 'rosa.diaz',
    display_name: 'Rosa Díaz',
    requested_role: 'guardian',
  },
  allowed_actions: ['approve', 'reject'],
  version: 1,
};

const OWNER = {
  id: '00000000-0000-4000-8000-0000000000a9',
  display_name: 'Demo Owner',
};

export const MIXED_ITEMS: ApprovalRequestItem[] = [
  { ...PENDING_REGISTRATION, priority: 'high' },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c2',
    status: 'approved',
    requester: {
      id: '00000000-0000-4000-8000-0000000000a2',
      display_name: 'Luis S',
    },
    subject: {
      username: 'luis.s',
      display_name: 'Luis S',
      requested_role: 'student',
    },
    resolved_at: '2026-10-19T17:00:00Z',
    resolved_by: OWNER,
    allowed_actions: [],
    version: 2,
  },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c3',
    status: 'rejected',
    priority: 'low',
    subject: {
      username: 'ana.p',
      display_name: 'Ana P',
      requested_role: 'guardian',
    },
    resolved_at: '2026-10-19T17:05:00Z',
    resolved_by: OWNER,
    reason: 'Unknown family.',
    allowed_actions: [],
    version: 2,
  },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c4',
    request_type: 'test.sample',
    status: 'changes_requested',
    requester: null,
    subject: {},
    reason: 'Add the date.',
    allowed_actions: ['reject'],
    version: 2,
  },
  {
    ...PENDING_REGISTRATION,
    id: '00000000-0000-4000-8000-0000000000c5',
    status: 'cancelled',
    subject: {
      username: 'mia.r',
      display_name: 'Mía R',
      requested_role: 'student',
    },
    resolved_at: '2026-10-19T18:00:00Z',
    allowed_actions: [],
    version: 2,
  },
];
