import type { Session } from '@/features/auth/session';

// Storybook only (#425): a signed-in session for the stories that show the
// Payroll tabs or read the center's time zone.
export const FINANCE_SESSION: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.finance',
    display_name: 'Demo Finance',
    privacy_notice_required: false,
    preferences: { language: null, theme: null },
    center_time_zone: 'America/Mexico_City',
    roles: ['finance_staff'],
    permissions: ['payroll.view', 'payroll.manage'],
  },
};

/** A tutor reading their own pay (M7-10b). */
export const TUTOR_SESSION: Session = {
  status: 'signedIn',
  user: {
    id: '70000000-0000-4000-8000-000000000001',
    username: 'demo.tutor',
    display_name: 'Demo Tutor',
    privacy_notice_required: false,
    preferences: { language: null, theme: null },
    center_time_zone: 'America/Mexico_City',
    roles: ['tutor'],
    permissions: ['tutor.view'],
  },
};
