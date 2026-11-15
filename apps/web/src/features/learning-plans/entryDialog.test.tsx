import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import { meAs, stubApi } from '@/test/api-stub';
import { renderApp, settle, type RenderedApp } from '@/test/render-app';
import { STUDENT_ID, TUTOR } from './fixtures';

// #310 A4 (E4 (a)): in the tutor's My schedule dialog, a confirmed or
// completed class lists "Learning plans" per student (named "Learning plans
// of {name}") next to #308's links; nothing for other states or for the
// family audience. The #308 links are unchanged.

const NOW = Date.parse('2026-11-09T16:30:00Z');
const NAME = 'José Ñúñez Pérez';
const OTHER_ID = '00000000-0000-4000-8000-000000310e02';

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
    participants: [
      { student_id: STUDENT_ID, display_name: NAME },
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
  return [...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a')];
}

const named = (links: HTMLAnchorElement[], name: string) =>
  links.find((a) => a.getAttribute('aria-label') === name);

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('My schedule: "Learning plans of {name}" (A4)', () => {
  it.each([
    ['confirmed', true],
    ['completed', true],
    ['offered', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ])('a tutor %s entry: %s', async (state, shown) => {
    const links = await dialogFor(state);
    const plans = named(links, `Learning plans of ${NAME}`);
    expect(Boolean(plans)).toBe(shown);
    expect(Boolean(named(links, 'Learning plans of Ana García'))).toBe(shown);
    // #308's link stays beside it.
    expect(Boolean(named(links, `Academic history of ${NAME}`))).toBe(shown);
    if (plans) {
      expect(plans.textContent).toBe('Learning plans');
      expect(plans.getAttribute('href')).toBe(
        `/students/${STUDENT_ID}/learning-plans`,
      );
    }
  });

  it('nothing for the family audience', async () => {
    const links = await dialogFor('confirmed', 'family');
    expect(named(links, `Learning plans of ${NAME}`)).toBeUndefined();
  });
});
