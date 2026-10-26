import type { Session } from '@/features/auth/session';
import { tutorSession } from '@/features/assignments/storyKit';

// Storybook only (#315 H2): the shared viewports, the "no sideways scroll"
// check and the fetch stub of #313, plus the reviewer sessions.

export {
  guardianSession,
  noSideScroll,
  problemResponse,
  stubFetch,
  tutorSession,
  VIEWPORTS,
} from '@/features/assignments/storyKit';

export const reviewerSession: Session = {
  status: 'signedIn',
  user: {
    ...(tutorSession.status === 'signedIn' ? tutorSession.user : ({} as never)),
    username: 'demo.coordinator',
    display_name: 'Laura Méndez',
    roles: ['academic_coordinator'],
    permissions: ['student.view', 'progress_report.review'],
  },
};

export const adminTutorSession: Session = {
  status: 'signedIn',
  user: {
    ...(tutorSession.status === 'signedIn' ? tutorSession.user : ({} as never)),
    username: 'demo.admin.tutor',
    display_name: 'Raúl Ortega',
    roles: ['administrator', 'tutor'],
    permissions: ['student.view', 'tutor.view', 'progress_report.review'],
  },
};
