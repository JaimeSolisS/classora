import type { Session } from '@/features/auth/session';

// Storybook only (#366): the signed-in sessions the billing stories render
// under (the Billing tabs show the sections the user may open).

const user = {
  id: '00000000-0000-4000-8000-000000000001',
  username: 'demo.finance',
  display_name: 'Demo Finance',
  privacy_notice_required: false,
  preferences: { language: null, theme: null },
  center_time_zone: 'America/Mexico_City',
} as const;

/** Finance Staff: every billing section, and the families list. */
export const FINANCE_SESSION: Session = {
  status: 'signedIn',
  user: {
    ...user,
    roles: ['finance_staff'],
    permissions: [
      'student.view',
      'guardian.view',
      'billing.view',
      'billing.manage',
    ],
  },
};

/** billing.view only: the Unbilled and Settings tabs, read-only. */
export const VIEWER_SESSION: Session = {
  status: 'signedIn',
  user: { ...user, roles: ['finance_staff'], permissions: ['billing.view'] },
};
