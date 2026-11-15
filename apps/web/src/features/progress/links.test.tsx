import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { JOSE, JOSE_ID } from '@/features/students/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import { meAs, stubApi, TEST_USER } from '@/test/api-stub';
import {
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';

// #322 A5, A6: the student page's "Progress" link for evaluation.manage
// holders and tutors only (never Finance, guardians or students), and My
// schedule's per-participant "Progress of {name}" for a tutor's confirmed
// or completed entry only (never other states or the family audience), the
// earlier links unchanged.

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });
const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'evaluation.manage'],
);
const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
const FINANCE = user(['finance_staff'], ['student.view', 'schedule.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
const STUDENT = user(['student'], ['student.view']);

let app: RenderedApp | undefined;

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the student page link (A5)', () => {
  it.each([
    ['a Coordinator (evaluation.manage)', COORDINATOR, true],
    ['a tutor', TUTOR, true],
    ['Finance', FINANCE, false],
    ['a guardian', GUARDIAN, false],
    ['a student', STUDENT, false],
  ] as const)('%s: %s', async (_who, as, shown) => {
    stubApi({
      'GET /api/v1/auth/me': meAs(as),
      [`GET /api/v1/students/${JOSE_ID}`]: () => jsonResponse(200, JOSE),
    });
    app = await renderApp(`/students/${JOSE_ID}`);
    const main = () => app!.container.querySelector('main')!;
    await settleUntil(() =>
      main().querySelector('h1')?.textContent?.includes('José'),
    );
    const link = [...main().querySelectorAll('a')].find(
      (anchor) =>
        anchor.getAttribute('href') === `/students/${JOSE_ID}/progress`,
    );
    expect(Boolean(link)).toBe(shown);
    if (link) {
      expect(link.textContent).toBe('Progress');
    }
  });
});

describe('My schedule: "Progress of {name}" (A6)', () => {
  const NOW = Date.parse('2026-11-09T16:30:00Z');
  const NAME = 'José Ñúñez Pérez';
  const OTHER_ID = '00000000-0000-4000-8000-000000322e02';

  async function dialogFor(
    state: string,
    audience: 'tutor' | 'family' = 'tutor',
  ) {
    stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const item = {
      ...tutorItem(),
      state,
      starts_at: '2026-11-09T16:00:00Z',
      participants: [
        { student_id: JOSE_ID, display_name: NAME },
        { student_id: OTHER_ID, display_name: 'Ana García' },
      ],
    } as ReturnType<typeof tutorItem>;
    const entry =
      audience === 'tutor'
        ? ({ audience: 'tutor', item } as const)
        : ({
            audience: 'family',
            item: {
              id: item.id,
              kind: item.kind,
              state: item.state,
              starts_at: item.starts_at,
              ends_at: item.ends_at,
              subject: item.subject,
              tutor: { display_name: 'Carmen Ruiz' },
              classroom: null,
              attendance: null,
            },
          } as never);
    app = await renderApp('/', [
      {
        path: '/',
        element: createElement(EntryDialog, {
          entry,
          context: {
            t: i18n.getFixedT('en', 'common'),
            timeZone: 'America/Mexico_City',
            language: 'en',
          },
          onClose: () => undefined,
          onReturnFocus: () => undefined,
          now: () => NOW,
        }),
      },
    ]);
    await settle();
    return [
      ...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a'),
    ];
  }

  const named = (anchors: HTMLAnchorElement[], name: string) =>
    anchors.find((a) => a.getAttribute('aria-label') === name);

  it.each([
    ['confirmed', true],
    ['completed', true],
    ['offered', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ])('a tutor %s entry: %s', async (state, shown) => {
    const anchors = await dialogFor(state);
    const progress = named(anchors, `Progress of ${NAME}`);
    expect(Boolean(progress)).toBe(shown);
    expect(Boolean(named(anchors, 'Progress of Ana García'))).toBe(shown);
    // The earlier links stay beside it.
    expect(Boolean(named(anchors, `Progress reports of ${NAME}`))).toBe(shown);
    expect(Boolean(named(anchors, `Learning plans of ${NAME}`))).toBe(shown);
    expect(Boolean(named(anchors, `Academic history of ${NAME}`))).toBe(shown);
    if (progress) {
      expect(progress.textContent).toBe('Progress');
      expect(progress.getAttribute('href')).toBe(
        `/students/${JOSE_ID}/progress`,
      );
    }
  });

  it('nothing for the family audience', async () => {
    const anchors = await dialogFor('confirmed', 'family');
    expect(named(anchors, `Progress of ${NAME}`)).toBeUndefined();
  });
});
