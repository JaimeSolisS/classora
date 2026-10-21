import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  OTHER_USER,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { AssignmentOfferItem } from './api';
import { DECLINED_OFFER, NOW, PENDING_OFFER } from './fixtures';
import {
  deadlineText,
  isDueSoon,
  noteTooLong,
  sessionTimes,
  staleAnswer,
} from './rules';

// #52 H1, H4: the tutor's "My offers" page: the role requirement, the
// cards, accept and decline, and the 409 handling; the pure rules (the
// due-soon boundary, deadline formatting across a Tijuana DST change).

const LIST = 'GET /api/v1/assignment-offers';
const ACCEPT = `POST /api/v1/assignment-offers/${PENDING_OFFER.id}/accept`;
const DECLINE = `POST /api/v1/assignment-offers/${PENDING_OFFER.id}/decline`;

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

const page = (items: AssignmentOfferItem[]) =>
  jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });

function stub(routes: Record<string, Responder | Responder[]> = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(OTHER_USER),
    [LIST]: (request) =>
      new URL(request.url).searchParams.get('status') === 'answered'
        ? page([DECLINED_OFFER])
        : page([PENDING_OFFER]),
    ...routes,
  });
}

function typeNote(field: HTMLTextAreaElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** The `status` query of the first list request. */
const firstListStatus = (api: ApiStub) => {
  const [request] = api.fetch.mock.calls.find(
    ([candidate]) =>
      new URL(candidate.url).pathname === '/api/v1/assignment-offers',
  )!;
  return new URL(request.url).searchParams.get('status');
};

const buttonByText = (root: ParentNode, text: string) =>
  [...root.querySelectorAll('button')].find((b) => b.textContent === text)!;

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rules', () => {
  it('is due soon under 24 hours, not at 24 hours or after the deadline', () => {
    const now = new Date('2031-01-03T12:00:00Z');
    expect(isDueSoon('2031-01-04T11:59:00Z', now)).toBe(true);
    expect(isDueSoon('2031-01-04T12:00:00Z', now)).toBe(false);
    expect(isDueSoon('2031-01-03T11:59:00Z', now)).toBe(false);
  });

  it('formats the deadline and times in the center zone across a DST change', () => {
    // America/Tijuana springs forward on 2026-03-08: 10:00 is UTC-8 before
    // and UTC-7 after.
    expect(deadlineText('2026-03-07T18:00:00Z', 'America/Tijuana', 'en')).toBe(
      'Mar 7, 2026, 10:00 AM',
    );
    expect(deadlineText('2026-03-09T17:00:00Z', 'America/Tijuana', 'es')).toBe(
      '9 mar 2026, 10:00',
    );
    expect(
      sessionTimes(
        { starts_at: '2026-03-09T17:00:00Z', ends_at: '2026-03-09T18:30:00Z' },
        'America/Tijuana',
        'es',
      ),
    ).toEqual({ date: '9 mar 2026', start: '10:00', end: '11:30' });
  });

  it('limits the note to 500 characters once trimmed', () => {
    expect(noteTooLong(`  ${'a'.repeat(500)}  `)).toBe(false);
    expect(noteTooLong('a'.repeat(501))).toBe(true);
  });

  it('treats only offer_not_pending and offer_expired as stale answers', () => {
    const error = (code: string) =>
      new ApiError({ code, status: 409, requestId: 'r' });
    expect(staleAnswer(error('offer_not_pending'))).toBe('offer_not_pending');
    expect(staleAnswer(error('offer_expired'))).toBe('offer_expired');
    expect(staleAnswer(error('version_conflict'))).toBeNull();
    expect(staleAnswer(new Error('x'))).toBeNull();
  });
});

describe('/offers', () => {
  it('needs the tutor role: no item and the forbidden page for others', async () => {
    const api = stub({ 'GET /api/v1/auth/me': meAs(TEST_USER) });
    app = await renderApp('/offers');

    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).not.toContain('My offers');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('shows a tutor the pending card with the deadline and the zone', async () => {
    const api = stub();
    app = await renderApp('/offers');

    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toContain('My offers');
    expect(main().querySelector('h1')?.textContent).toBe('My offers');
    expect(firstListStatus(api)).toBe('pending');
    const text = main().textContent ?? '';
    expect(text).toContain('Álgebra (Secundaria)');
    expect(text).toContain('Jan 6, 2031, 10:00 AM–11:00 AM');
    expect(text).toContain('Aula 1');
    expect(text).toContain('Ana López');
    expect(text).toContain('Answer by Jan 5, 2031, 4:00 AM');
    expect(text).toContain('America/Mexico_City');
    const selected = main().querySelector('[role="tab"][aria-selected="true"]');
    expect(selected?.textContent).toBe('Pending');
  });

  it('accepts after confirmation, announces it and refreshes', async () => {
    const api = stub({
      [ACCEPT]: () =>
        jsonResponse(200, { ...PENDING_OFFER, status: 'accepted' }),
    });
    app = await renderApp('/offers');

    await click(buttonByText(main(), 'Accept'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Accept this session?');
    await click(buttonNamed(dialog, 'Accept'));

    expect(api.calls(ACCEPT)).toHaveLength(1);
    expect(openDialog()).toBeNull();
    expect(main().querySelector('[role="status"]')?.textContent).toContain(
      'Accepted: Álgebra',
    );
    expect(api.calls(LIST).length).toBeGreaterThan(1);
  });

  it('a 409 offer_expired shows its message and refreshes the list', async () => {
    const api = stub({
      [LIST]: [() => page([PENDING_OFFER]), () => page([])],
      [ACCEPT]: () => problem(409, 'offer_expired'),
    });
    app = await renderApp('/offers');

    await click(buttonByText(main(), 'Accept'));
    await click(buttonNamed(openDialog()!, 'Accept'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(alerts(main())).toContain(
      'The deadline to answer this offer has passed.',
    );
  });

  it('declines with the trimmed note', async () => {
    const api = stub({
      [DECLINE]: () =>
        jsonResponse(200, { ...PENDING_OFFER, status: 'declined' }),
    });
    app = await renderApp('/offers');

    await click(buttonByText(main(), 'Decline'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('0 of 500 characters');
    typeNote(dialog.querySelector('textarea')!, '  Otra clase  ');
    expect(dialog.textContent).toContain('10 of 500 characters');
    await click(buttonByText(dialog, 'Decline'));

    expect(api.calls(DECLINE)[0]!.body).toEqual({ note: 'Otra clase' });
    expect(openDialog()).toBeNull();
    expect(main().querySelector('[role="status"]')?.textContent).toContain(
      'Declined: Álgebra',
    );
  });

  it('a too-long note stays in the dialog', async () => {
    const api = stub();
    app = await renderApp('/offers');

    await click(buttonByText(main(), 'Decline'));
    const dialog = openDialog()!;
    typeNote(dialog.querySelector('textarea')!, 'a'.repeat(501));
    await click(buttonByText(dialog, 'Decline'));

    expect(api.calls(DECLINE)).toHaveLength(0);
    expect(dialog.textContent).toContain(
      'The note can be at most 500 characters long.',
    );
  });

  it('the Answered tab lists answered offers with the note', async () => {
    const api = stub();
    app = await renderApp('/offers?tab=answered');

    expect(firstListStatus(api)).toBe('answered');
    const text = main().textContent ?? '';
    expect(text).toContain('Declined');
    expect(text).toContain('Note: Tengo otra clase a esa hora.');
    expect(buttonByText(main(), 'Accept')).toBeUndefined();
  });

  it('pins "now" for the due-soon badge in the fixtures', () => {
    expect(isDueSoon(PENDING_OFFER.respond_by, NOW)).toBe(false);
  });
});
