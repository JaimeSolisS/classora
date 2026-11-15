import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import { meAs, stubApi } from '@/test/api-stub';
import { renderApp, settle, type RenderedApp } from '@/test/render-app';
import { STUDENT_ID, TUTOR } from './fixtures';

// #313 A4: in the tutor's My schedule dialog, a confirmed or completed
// class offers one session-level "Set homework" (the form for this class);
// nothing for other states or for the family audience. The #76-#310 links
// are unchanged.

const NOW = Date.parse('2026-11-09T16:30:00Z');
const NAME = 'José Ñúñez Pérez';

let app: RenderedApp | undefined;

async function dialogFor(
  state: string,
  audience: 'tutor' | 'family' = 'tutor',
) {
  stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
  const item = {
    ...tutorItem(),
    state,
    starts_at: '2026-11-09T16:00:00Z',
    participants: [{ student_id: STUDENT_ID, display_name: NAME }],
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
  return {
    item,
    links: [
      ...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a'),
    ],
  };
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('My schedule: "Set homework" (A4)', () => {
  it.each([
    ['confirmed', true],
    ['completed', true],
    ['offered', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ])('a tutor %s entry: %s', async (state, shown) => {
    const { item, links } = await dialogFor(state);
    const homework = links.filter((a) => a.textContent === 'Set homework');
    expect(homework).toHaveLength(shown ? 1 : 0);
    if (shown) {
      expect(homework[0]!.getAttribute('href')).toBe(
        `/assignments/new?sessionId=${item.id}`,
      );
      // The #308 / #310 links stay beside it.
      expect(
        links.some(
          (a) => a.getAttribute('aria-label') === `Learning plans of ${NAME}`,
        ),
      ).toBe(true);
    }
  });

  it('nothing for the family audience', async () => {
    const { links } = await dialogFor('confirmed', 'family');
    expect(links.some((a) => a.textContent === 'Set homework')).toBe(false);
  });
});
