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
  CANCELLED_SESSION,
  CONFIRMED_SESSION,
  DECLINED,
  DECLINED_SUBSTITUTION,
  NEW_SUBSTITUTION,
  PENDING_SUBSTITUTION,
  SUBSTITUTED_SESSION,
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
  canCancel,
  canSubstitute,
  conflictsByDate,
  isStaleSessionError,
  offerBody,
  offerServerErrors,
  respondByInstant,
  seriesOfferBody,
  substitutionBody,
  validateOffer,
} from './rules';
import { ApiError } from '@/app/api';

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
const SUBSTITUTIONS = `GET /api/v1/class-sessions/${ID}/substitutions`;
const SUBSTITUTE = `POST /api/v1/class-sessions/${ID}/substitutions`;
const WITHDRAW = `POST /api/v1/class-sessions/${ID}/substitutions/${PENDING_SUBSTITUTION.id}/withdraw`;
const CANCEL = `POST /api/v1/class-sessions/${ID}/cancel`;

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
    [SUBSTITUTIONS]: () => jsonResponse(200, { items: [] }),
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

// #55 H1, H4: "Find a substitute", the pending substitution and its
// history, "Cancel session" and the cancelled banner; the rules (the
// panels' visibility, the body with a deadline in Tijuana across the DST
// change, the field errors, the refused writes that reload the session).

const MANAGER: SessionUserBody = {
  ...OWNER,
  permissions: [...OWNER.permissions, 'schedule.manage'],
};

function problem(status: number, code: string): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status,
      code,
      request_id: 'req-1',
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function typeInto(field: HTMLTextAreaElement, value: string) {
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

describe('substitution and cancel rules (#55)', () => {
  const now = new Date('2031-01-06T15:59:00Z');
  const later = new Date('2031-01-06T16:00:00Z');

  it('shows the substitute panel for a confirmed future session only', () => {
    expect(canSubstitute(CONFIRMED_SESSION, now, true)).toBe(true);
    expect(canSubstitute(CONFIRMED_SESSION, later, true)).toBe(false);
    expect(canSubstitute(CONFIRMED_SESSION, now, false)).toBe(false);
    expect(canSubstitute(SUBSTITUTED_SESSION, now, true)).toBe(false);
    for (const state of ['unassigned', 'offered', 'cancelled'] as const) {
      expect(canSubstitute({ ...CONFIRMED_SESSION, state }, now, true)).toBe(
        false,
      );
    }
  });

  it('offers "Cancel session" for the cancellable states before the start', () => {
    for (const state of [
      'unassigned',
      'offered',
      'confirmed',
      'conflict',
    ] as const) {
      expect(canCancel({ ...CONFIRMED_SESSION, state }, now, true)).toBe(true);
    }
    for (const state of [
      'completed',
      'cancelled',
      'skipped_closure',
    ] as const) {
      expect(canCancel({ ...CONFIRMED_SESSION, state }, now, true)).toBe(false);
    }
    expect(canCancel(CONFIRMED_SESSION, later, true)).toBe(false);
    expect(canCancel(CONFIRMED_SESSION, now, false)).toBe(false);
  });

  it('builds the substitution body; the deadline is local to Tijuana across DST', () => {
    expect(
      substitutionBody(
        {
          tutorId: 'tutor-2',
          deadlineDate: '2026-03-09',
          deadlineTime: '10:00',
          reason: '  Enferma  ',
        },
        5,
        'America/Tijuana',
      ),
    ).toEqual({
      substitute_tutor_user_id: 'tutor-2',
      version: 5,
      reason: 'Enferma',
      respond_by: '2026-03-09T17:00:00.000Z',
    });
    expect(
      substitutionBody(
        {
          tutorId: 'tutor-2',
          deadlineDate: '2026-03-07',
          deadlineTime: '10:00',
          reason: 'x',
        },
        5,
        'America/Tijuana',
      ).respond_by,
    ).toBe('2026-03-07T18:00:00.000Z');
  });

  it('needs a reason of at most 1000 characters', () => {
    const values = {
      tutorId: 't',
      deadlineDate: '',
      deadlineTime: '',
      reason: '   ',
    };
    expect(validateOffer(values, { requireReason: true })).toEqual({
      reason: 'scheduling.substitution.validation.reasonRequired',
    });
    expect(
      validateOffer(
        { ...values, reason: 'a'.repeat(1001) },
        { requireReason: true },
      ),
    ).toEqual({ reason: 'scheduling.substitution.validation.reasonTooLong' });
    expect(validateOffer(values)).toEqual({});
  });

  it('maps the substitute and reason field errors', () => {
    expect(
      offerServerErrors([
        {
          path: ['body', 'substitute_tutor_user_id'],
          code: 'substitute_is_current_tutor',
          message: '',
        },
        { path: ['body', 'reason'], code: 'reason_too_long', message: '' },
      ]),
    ).toEqual({
      tutor: 'errors:substitute_is_current_tutor',
      reason: 'scheduling.substitution.validation.reasonTooLong',
    });
  });

  it('reloads after the refused writes only', () => {
    const error = (code: string) =>
      new ApiError({ code, status: 409, requestId: 'r' });
    for (const code of [
      'class_session_not_substitutable',
      'class_session_not_cancellable',
      'version_conflict',
      'offer_not_pending',
    ]) {
      expect(isStaleSessionError(error(code))).toBe(true);
    }
    expect(isStaleSessionError(error('scheduling_conflict'))).toBe(false);
    expect(isStaleSessionError(new Error('x'))).toBe(false);
  });
});

describe('/schedule/sessions/:id substitutions and cancellation (#55)', () => {
  it('a confirmed session gets "Find a substitute"; the request sends the reason', async () => {
    const api = stub(ASSIGNER, {
      [DETAIL]: () => jsonResponse(200, CONFIRMED_SESSION),
      [SUBSTITUTE]: () => jsonResponse(201, NEW_SUBSTITUTION),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    expect(main().textContent).toContain('Find a substitute');
    expect(main().textContent).not.toContain('Cancel session');
    const select = main().querySelector<HTMLSelectElement>('form select')!;
    // The current tutor is not a choice.
    expect([...select.options].map((option) => option.value)).not.toContain(
      TUTORS[0]!.id,
    );
    choose(select, TUTORS[1]!.id);
    await click(buttonByText(main(), 'Offer to the substitute')!);
    expect(api.calls(SUBSTITUTE)).toHaveLength(0);
    expect(main().textContent).toContain('Give a reason.');

    typeInto(
      main().querySelector<HTMLTextAreaElement>('form textarea')!,
      ' Carmen está enferma ',
    );
    await click(buttonByText(main(), 'Offer to the substitute')!);

    expect(api.calls(SUBSTITUTE)[0]!.body).toEqual({
      substitute_tutor_user_id: TUTORS[1]!.id,
      version: 5,
      reason: 'Carmen está enferma',
    });
    expect(main().textContent).toContain(
      'Offered to Diego Torres as a substitute.',
    );
  });

  it('no panel without schedule.assign; a pending substitution shows its card and history', async () => {
    stub(
      { ...TEST_USER, permissions: ['schedule.view'] },
      { [DETAIL]: () => jsonResponse(200, CONFIRMED_SESSION) },
    );
    app = await renderApp(`/schedule/sessions/${ID}`);
    expect(main().textContent).not.toContain('Find a substitute');
    app.unmount();

    const api = stub(ASSIGNER, {
      [DETAIL]: () => jsonResponse(200, SUBSTITUTED_SESSION),
      [SUBSTITUTIONS]: () =>
        jsonResponse(200, {
          items: [PENDING_SUBSTITUTION, DECLINED_SUBSTITUTION],
        }),
      [WITHDRAW]: () =>
        jsonResponse(200, { ...PENDING_SUBSTITUTION, status: 'withdrawn' }),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);
    const text = main().textContent ?? '';
    expect(text).not.toContain('Find a substitute');
    expect(text).toContain('Pending substitution');
    expect(text).toContain('Substitution pending: Diego Torres, answer by');
    expect(text).toContain('Carmen Ruiz → Diego Torres');
    expect(text).toContain('Reason: Carmen está enferma.');
    expect(text).toContain('Note: No puedo ese día.');

    await click(buttonByText(main(), 'Withdraw substitution')!);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    await click(buttonByText(dialog, 'Withdraw substitution')!);
    expect(api.calls(WITHDRAW)).toHaveLength(1);
  });

  it('a 409 class_session_not_substitutable shows its message and reloads', async () => {
    const api = stub(ASSIGNER, {
      [DETAIL]: () => jsonResponse(200, CONFIRMED_SESSION),
      [SUBSTITUTE]: () => problem(409, 'class_session_not_substitutable'),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);
    const before = api.calls(DETAIL).length;

    choose(
      main().querySelector<HTMLSelectElement>('form select')!,
      TUTORS[1]!.id,
    );
    typeInto(
      main().querySelector<HTMLTextAreaElement>('form textarea')!,
      'Viaje',
    );
    await click(buttonByText(main(), 'Offer to the substitute')!);

    expect(main().textContent).toContain(
      "This session can't be given to a substitute now.",
    );
    expect(api.calls(DETAIL).length).toBeGreaterThan(before);
  });

  it('cancels with a reason; focus starts on the reason, not the destructive button', async () => {
    const api = stub(MANAGER, {
      [DETAIL]: [
        () => jsonResponse(200, CONFIRMED_SESSION),
        () => jsonResponse(200, CANCELLED_SESSION),
      ],
      [CANCEL]: () => jsonResponse(200, CANCELLED_SESSION),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    await click(buttonByText(main(), 'Cancel session')!);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.textContent).toContain(
      'The tutor and the family are notified',
    );
    expect(document.activeElement).toBe(dialog.querySelector('textarea'));
    await click(buttonByText(dialog, 'Cancel the session')!);
    expect(api.calls(CANCEL)).toHaveLength(0);
    expect(dialog.textContent).toContain('Give a reason.');

    typeInto(dialog.querySelector('textarea')!, '  Cierre  ');
    await click(buttonByText(dialog, 'Cancel the session')!);

    expect(api.calls(CANCEL)[0]!.body).toEqual({
      version: 5,
      reason: 'Cierre',
    });
    expect(main().textContent).toContain('Cancelled');
    expect(main().textContent).toContain('Reason: Cierre por mantenimiento.');
    expect(buttonByText(main(), 'Cancel session')).toBeUndefined();
  });

  it('a 409 class_session_not_cancellable stays in the dialog and reloads', async () => {
    const api = stub(MANAGER, {
      [DETAIL]: () => jsonResponse(200, CONFIRMED_SESSION),
      [CANCEL]: () => problem(409, 'class_session_not_cancellable'),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);
    const before = api.calls(DETAIL).length;

    await click(buttonByText(main(), 'Cancel session')!);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    typeInto(dialog.querySelector('textarea')!, 'Cierre');
    await click(buttonByText(dialog, 'Cancel the session')!);

    expect(dialog.textContent).toContain("This session can't be cancelled");
    expect(api.calls(DETAIL).length).toBeGreaterThan(before);
  });

  it('a cancelled session shows the banner and no actions', async () => {
    stub(MANAGER, {
      [DETAIL]: () => jsonResponse(200, CANCELLED_SESSION),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);

    const banner = [...main().querySelectorAll('h2')].find(
      (h) => h.textContent === 'Cancelled',
    );
    expect(banner).toBeDefined();
    expect(main().textContent).toContain('Cancelled on');
    expect(buttonByText(main(), 'Cancel session')).toBeUndefined();
    expect(main().textContent).not.toContain('Find a substitute');
  });
});
