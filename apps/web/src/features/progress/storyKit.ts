import type { Session } from '@/features/auth/session';
import {
  guardianSession,
  noSideScroll,
  problemResponse,
  stubFetch,
  studentSession,
  tutorSession,
  VIEWPORTS,
} from '@/features/assignments/storyKit';

// Storybook only (#322 G2): the sessions, the reads answered by path
// through #313's fetch stub (no request leaves a story), the shared
// viewports and the "no sideways scroll" check.

export {
  guardianSession,
  noSideScroll,
  problemResponse,
  studentSession,
  tutorSession,
  VIEWPORTS,
};

export const coordinatorSession: Session = {
  status: 'signedIn',
  user: {
    ...(tutorSession.status === 'signedIn' ? tutorSession.user : ({} as never)),
    username: 'demo.coordinator',
    display_name: 'Laura Méndez',
    roles: ['academic_coordinator'],
    permissions: ['student.view', 'evaluation.manage', 'schedule.view'],
  },
};

export const json = (body: unknown) => () =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

/** Answers each path's suffix with a fresh response; others never resolve. */
export function stubPaths(answers: Record<string, () => Response>): () => void {
  return stubFetch((path) => {
    const entry = Object.entries(answers).find(([suffix]) =>
      path.endsWith(suffix),
    );
    return entry ? entry[1]() : null;
  });
}
