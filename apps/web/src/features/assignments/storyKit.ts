import { expect } from 'storybook/test';
import type { Session } from '@/features/auth/session';

// Storybook only (#313 G2): the viewports, the "no sideways scroll" check
// of the 320 / 390 px variants, the signed-in sessions, and a fetch stub
// for the states only a response gives (no request leaves a story).

export const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

export async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const base: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.tutor',
    display_name: 'Carmen Ruiz',
    roles: ['tutor'],
    permissions: ['student.view', 'tutor.view'],
    privacy_notice_required: false,
    preferences: { language: 'es', theme: 'system' },
    center_time_zone: 'America/Mexico_City',
  },
};

export const tutorSession = base;

export const guardianSession: Session = {
  ...base,
  user: {
    ...base.user,
    username: 'demo.guardian',
    display_name: 'Laura Pérez',
    roles: ['guardian'],
    permissions: ['student.view', 'guardian.view'],
  },
};

export const studentSession: Session = {
  ...base,
  user: {
    ...base.user,
    username: 'demo.student',
    display_name: 'José Ñúñez Pérez',
    roles: ['student'],
    permissions: ['student.view'],
  },
};

/**
 * Replaces fetch for one story: `respond` answers by path; anything else
 * never resolves. Returns the cleanup Storybook runs after the story.
 */
export function stubFetch(
  respond: (path: string) => Response | null,
): () => void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const original = window.fetch;
  window.fetch = (input: RequestInfo | URL) => {
    const url = new URL(
      input instanceof Request ? input.url : String(input),
      window.location.origin,
    );
    const answer = respond(url.pathname);
    return answer ? Promise.resolve(answer) : new Promise(() => {});
  };
  return () => {
    window.fetch = original;
  };
}

/** A Problem Details response. */
export function problemResponse(status: number, code: string): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `story-${code}`,
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}
