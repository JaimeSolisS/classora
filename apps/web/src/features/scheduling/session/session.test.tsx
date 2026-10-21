import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import {
  CREATED,
  SOFT_CONFLICTS,
  TUTOR_SUGGESTIONS,
  TUTORS,
} from '../create/fixtures';
import {
  ACCEPTED_SERIES,
  DECLINED,
  NEW_OFFER,
  NEW_SERIES_OFFER,
  OFFERED_SESSION,
  PENDING_SERIES,
  SERIES,
  SERIES_CONFLICTS,
  SERIES_ID,
  SERIES_ROW,
  UNASSIGNED_SESSION,
} from './fixtures';
import {
  conflictsByDate,
  offerBody,
  offerServerErrors,
  respondByInstant,
  seriesOfferBody,
  validateOffer,
} from './rules';

// #52 H2, H4: the staff session page (summary, history, the offer panel
// with the #51 suggestions, the #48 conflict panel and the #49 override),
// and the offer form's rules (the deadline conversion in the center zone).

const ID = CREATED.id;
const DETAIL = `GET /api/v1/class-sessions/${ID}`;
const OFFERS = `GET /api/v1/class-sessions/${ID}/offers`;
const OFFER = `POST /api/v1/class-sessions/${ID}/offers`;
const REPLACEMENTS = `GET /api/v1/class-sessions/${ID}/replacement-tutors`;
const SERIES_DETAIL = `GET /api/v1/class-series/${SERIES_ID}`;
const SERIES_OFFER = `POST /api/v1/class-series/${SERIES_ID}/offers`;

const ASSIGNER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'schedule.assign', 'tutor.view'],
};
const OWNER: SessionUserBody = {
  ...ASSIGNER,
  permissions: [...ASSIGNER.permissions, 'schedule.override'],
};

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function stub(
  user: SessionUserBody = ASSIGNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [DETAIL]: () => jsonResponse(200, UNASSIGNED_SESSION),
    [OFFERS]: () => jsonResponse(200, { items: [DECLINED] }),
    'GET /api/v1/tutors': () =>
      jsonResponse(200, {
        items: TUTORS.map((t) => ({
          user_id: t.id,
          username: t.id.slice(-4),
          display_name: t.name,
          state: 'active',
          email: null,
          phone: null,
        })),
        total: TUTORS.length,
        limit: 100,
        offset: 0,
      }),
    [REPLACEMENTS]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
    ...routes,
  });
}

function choose(select: HTMLSelectElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

const buttonByText = (root: ParentNode, text: string) =>
  [...root.querySelectorAll('button')].find((b) => b.textContent === text);

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

describe('offer rules', () => {
  it('converts the deadline from the center zone, across a Tijuana DST change', () => {
    const values = {
      tutorId: 't',
      deadlineDate: '2026-03-07',
      deadlineTime: '10:00',
    };
    expect(respondByInstant(values, 'America/Tijuana')).toBe(
      '2026-03-07T18:00:00.000Z',
    );
    expect(
      respondByInstant(
        { ...values, deadlineDate: '2026-03-09' },
        'America/Tijuana',
      ),
    ).toBe('2026-03-09T17:00:00.000Z');
    expect(
      respondByInstant(
        { deadlineDate: '', deadlineTime: '' },
        'America/Tijuana',
      ),
    ).toBeNull();
  });

  it('builds the body with the version, an optional deadline and override', () => {
    expect(
      offerBody(
        { tutorId: 'tutor-1', deadlineDate: '', deadlineTime: '' },
        3,
        'America/Mexico_City',
      ),
    ).toEqual({ tutor_user_id: 'tutor-1', version: 3 });
    expect(
      offerBody(
        {
          tutorId: 'tutor-1',
          deadlineDate: '2031-01-05',
          deadlineTime: '09:30',
        },
        3,
        'America/Mexico_City',
        { codes: ['tutor_unavailable'], reason: 'Acordado' },
      ),
    ).toEqual({
      tutor_user_id: 'tutor-1',
      version: 3,
      respond_by: '2031-01-05T15:30:00.000Z',
      override: { codes: ['tutor_unavailable'], reason: 'Acordado' },
    });
  });

  it('needs a tutor and both deadline parts or neither', () => {
    expect(
      validateOffer({
        tutorId: '',
        deadlineDate: '2031-01-05',
        deadlineTime: '',
      }),
    ).toEqual({
      tutor: 'scheduling.offer.validation.tutorRequired',
      deadline: 'scheduling.offer.validation.deadlineIncomplete',
    });
    expect(
      validateOffer({ tutorId: 't', deadlineDate: '', deadlineTime: '' }),
    ).toEqual({});
  });

  it('builds the series body with the series version and the from-date', () => {
    expect(
      seriesOfferBody(
        { tutorId: 'tutor-1', deadlineDate: '', deadlineTime: '' },
        5,
        '2031-01-06',
        'America/Mexico_City',
      ),
    ).toEqual({
      tutor_user_id: 'tutor-1',
      version: 5,
      from_date: '2031-01-06',
    });
  });

  it('groups series conflicts by local date', () => {
    expect(
      conflictsByDate(SERIES_CONFLICTS).map((group) => [
        group.date,
        group.conflicts.map((c) => c.code),
      ]),
    ).toEqual([
      ['2031-01-13', ['tutor_unavailable', 'tutor_not_qualified']],
      ['2031-01-15', ['tutor_overlap']],
    ]);
  });

  it('maps the server field errors onto the fields', () => {
    expect(
      offerServerErrors([
        {
          path: ['body', 'tutor_user_id'],
          code: 'tutor_inactive',
          message: '',
        },
        {
          path: ['body', 'respond_by'],
          code: 'respond_by_invalid',
          message: '',
        },
      ]),
    ).toEqual({
      tutor: 'errors:tutor_inactive',
      deadline: 'errors:respond_by_invalid',
    });
  });
});

describe('/schedule/sessions/:id', () => {
  it('shows the summary and the history with the decline note', async () => {
    stub();
    app = await renderApp(`/schedule/sessions/${ID}`);

    expect(main().querySelector('h1')?.textContent).toBe('Session');
    const text = main().textContent ?? '';
    expect(text).toContain('Unassigned');
    expect(text).toContain('Álgebra (Secundaria)');
    expect(text).toContain('Offer history');
    expect(text).toContain('Declined');
    expect(text).toContain('Note: Tengo otra clase a esa hora.');
    expect(text).toContain('Offer to a tutor');
  });

  it('hides the offer panel without schedule.assign or once offered', async () => {
    stub({ ...TEST_USER, permissions: ['schedule.view'] });
    app = await renderApp(`/schedule/sessions/${ID}`);
    expect(main().textContent).not.toContain('Offer to a tutor');
    app.unmount();

    stub(ASSIGNER, {
      [DETAIL]: () => jsonResponse(200, OFFERED_SESSION),
      [OFFERS]: () => jsonResponse(200, { items: [] }),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);
    const text = main().textContent ?? '';
    expect(text).not.toContain('Offer to a tutor');
    expect(text).toContain('Pending offer');
    expect(text).toContain('Carmen Ruiz, answer by');
  });

  it('a suggestion fills the picker; the offer sends the version and shows warnings', async () => {
    const api = stub(ASSIGNER, {
      [OFFER]: () => jsonResponse(201, NEW_OFFER),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    await click(buttonByText(main(), 'Suggest tutors')!);
    expect(api.calls(REPLACEMENTS)).toHaveLength(1);
    await click(buttonNamed(main(), 'Choose Diego Torres'));
    const select = main().querySelector<HTMLSelectElement>('form select')!;
    expect(select.value).toBe(TUTORS[1]!.id);

    await click(buttonByText(main(), 'Offer the session')!);

    expect(api.calls(OFFER)[0]!.body).toEqual({
      tutor_user_id: TUTORS[1]!.id,
      version: 3,
    });
    expect(main().textContent).toContain('Offered to Diego Torres.');
    expect(main().textContent).toContain('Workload warnings');
  });

  it('a conflict shows the panel; the Owner overrides and resends', async () => {
    const conflict = () =>
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          code: 'scheduling_conflict',
          request_id: 'req-1',
          conflicts: SOFT_CONFLICTS,
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/problem+json' },
        },
      );
    const api = stub(OWNER, {
      [OFFER]: [conflict, () => jsonResponse(201, NEW_OFFER)],
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    choose(
      main().querySelector<HTMLSelectElement>('form select')!,
      TUTORS[0]!.id,
    );
    await click(buttonByText(main(), 'Offer the session')!);
    expect(main().querySelector('[role="alert"]')).not.toBeNull();

    await click(buttonByText(main(), 'Override and offer')!);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    const reason = dialog.querySelector('textarea')!;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value',
    )?.set;
    act(() => {
      setter?.call(reason, 'Acordado con la familia');
      reason.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(buttonByText(dialog, 'Override and offer')!);

    const second = api.calls(OFFER)[1]!.body as {
      override: { codes: string[] };
    };
    expect(second.override.codes).toEqual([
      ...new Set(SOFT_CONFLICTS.map((c) => c.code)),
    ]);
    expect(main().textContent).toContain('Offered to Diego Torres.');
  });

  it('a series occurrence offers this session only or the series from it', async () => {
    const api = stub(OWNER, {
      [DETAIL]: () => jsonResponse(200, SERIES_ROW),
      [SERIES_DETAIL]: () => jsonResponse(200, SERIES),
      [OFFER]: () => jsonResponse(201, NEW_OFFER),
      [SERIES_OFFER]: () => jsonResponse(201, NEW_SERIES_OFFER),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    expect(main().textContent).toContain('Part of a weekly series · no tutor');
    const radios = [
      ...main().querySelectorAll<HTMLInputElement>('input[type="radio"]'),
    ];
    expect(radios.map((r) => r.parentElement?.textContent)).toEqual([
      'This session only',
      'This and following sessions',
    ]);
    expect(radios[0]!.checked).toBe(true);

    await click(radios[1]!);
    choose(
      main().querySelector<HTMLSelectElement>('form select')!,
      TUTORS[1]!.id,
    );
    await click(buttonByText(main(), 'Offer the session')!);

    expect(api.calls(OFFER)).toHaveLength(0);
    expect(api.calls(SERIES_OFFER)[0]!.body).toEqual({
      tutor_user_id: TUTORS[1]!.id,
      version: 5,
      from_date: '2031-01-06',
    });
    expect(main().textContent).toContain(
      'Weekly series offered to Diego Torres.',
    );
  });

  it('a series 409 lists the conflicts by date, with no override', async () => {
    const conflict = () =>
      new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          code: 'scheduling_conflict',
          request_id: 'req-1',
          conflicts: SERIES_CONFLICTS,
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/problem+json' },
        },
      );
    stub(OWNER, {
      [DETAIL]: () => jsonResponse(200, SERIES_ROW),
      [SERIES_DETAIL]: () => jsonResponse(200, SERIES),
      [SERIES_OFFER]: conflict,
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    await click(
      main().querySelectorAll<HTMLInputElement>('input[type="radio"]')[1]!,
    );
    choose(
      main().querySelector<HTMLSelectElement>('form select')!,
      TUTORS[1]!.id,
    );
    await click(buttonByText(main(), 'Offer the session')!);

    const alert = main().querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain(
      'The series can’t be offered: 3 conflicts',
    );
    expect([...alert.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(
      ['Jan 13, 2031', 'Jan 15, 2031'],
    );
    expect(buttonByText(main(), 'Override and offer')).toBeUndefined();
  });

  it('no series choice once the series has a tutor; its assignment is shown', async () => {
    stub(ASSIGNER, {
      [DETAIL]: () => jsonResponse(200, SERIES_ROW),
      [SERIES_DETAIL]: () => jsonResponse(200, ACCEPTED_SERIES),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    expect(main().textContent).toContain(
      'Part of a weekly series · Diego Torres from Jan 6, 2031',
    );
    expect(main().textContent).toContain('Offer to a tutor');
    expect(main().querySelector('input[type="radio"]')).toBeNull();
    app.unmount();

    stub(ASSIGNER, {
      [DETAIL]: () => jsonResponse(200, SERIES_ROW),
      [SERIES_DETAIL]: () => jsonResponse(200, PENDING_SERIES),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);
    expect(main().textContent).toContain(
      'Part of a weekly series · offered to Diego Torres, answer by Jan 4, 2031, 3:00 AM',
    );
  });
});
