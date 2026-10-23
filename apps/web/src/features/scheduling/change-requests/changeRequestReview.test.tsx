import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { applyLanguage, i18n } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import type { ApprovalRequestItem } from '@/features/approvals/api';
import {
  changeRequestReviewLink,
  itemReviewLink,
  reviewLinkOf,
  approvalSummary,
} from '@/features/approvals/summary';
import {
  NOTIFICATION_LINKS,
  notificationMessage,
} from '@/features/notifications/messages';
import { formatInstant } from '@/lib/time';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog } from '@/test/dom';
import {
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { CLASSROOMS, SOFT_CONFLICTS } from '../create/fixtures';
import {
  APPROVED_EXCEPTION,
  APPROVED_RESULT,
  CHANGE_REQUEST_ID,
  PENDING_CANCELLATION,
  PENDING_RESCHEDULE,
  REJECTED,
  STARTED_CANCELLATION,
} from './fixtures';
import {
  approveBody,
  decisionReasonProblem,
  isStaleChangeRequestError,
  NO_ROOM,
  staleMessageKey,
} from './rules';

// #62 G1-G4: the staff review page (summary and policy outcome, approve
// with the room of a reschedule, approve as exception, reject, the 409
// messages and reload, the conflict panel and override), the inbox
// "Review" link and summary, and the two decision notifications.

const REVIEW = `GET /api/v1/change-requests/${CHANGE_REQUEST_ID}/review`;
const APPROVE = `POST /api/v1/change-requests/${CHANGE_REQUEST_ID}/approve`;
const REJECT = `POST /api/v1/change-requests/${CHANGE_REQUEST_ID}/reject`;
const PATH = `/schedule/change-requests/${CHANGE_REQUEST_ID}`;

const ADMIN: SessionUserBody = {
  ...TEST_USER,
  roles: ['administrator'],
  permissions: ['schedule.view', 'schedule.manage', 'schedule.assign'],
};
const OWNER: SessionUserBody = {
  ...ADMIN,
  roles: ['owner'],
  permissions: [...ADMIN.permissions, 'schedule.override'],
};

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function problem(
  status: number,
  code: string,
  extra: Record<string, unknown> = {},
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: 'req-1',
      ...extra,
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function stub(
  user: SessionUserBody = ADMIN,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [REVIEW]: () => jsonResponse(200, PENDING_CANCELLATION),
    'GET /api/v1/classrooms': () =>
      jsonResponse(200, {
        items: [
          ...CLASSROOMS,
          { id: '00000000-0000-4000-8000-0000000004d2', name: 'Aula 2' },
        ].map((room) => ({
          id: room.id,
          name: room.name,
          capacity: 10,
          is_active: true,
          version: 1,
          availability: [],
        })),
        total: 2,
        limit: 100,
        offset: 0,
      }),
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

const buttonByText = (root: ParentNode, text: string) =>
  [...root.querySelectorAll('button')].find((b) => b.textContent === text);

/** The lazy route needs a few more turns to load, then its queries. */
async function open(path: string): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  for (let i = 0; i < 60; i += 1) {
    const page = rendered.container.querySelector('main');
    const ready =
      page?.querySelector('h1') &&
      !page.querySelector('[aria-busy="true"]') &&
      (page.textContent?.includes('Decide') ||
        page.textContent?.includes('Decision') ||
        page.querySelector('table'));
    if (ready) {
      break;
    }
    await settle();
  }
  await settle();
  return rendered;
}

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

describe('change request rules', () => {
  it('builds the approve body: exception, reason and a reschedule room', () => {
    expect(approveBody(1, 'cancellation', { exception: false })).toEqual({
      version: 1,
      exception: false,
    });
    expect(
      approveBody(2, 'cancellation', {
        exception: true,
        reason: '  Enfermedad ',
        room: 'room-1',
      }),
    ).toEqual({ version: 2, exception: true, reason: 'Enfermedad' });
    expect(
      approveBody(1, 'reschedule', { exception: false, room: 'room-1' }),
    ).toEqual({ version: 1, exception: false, classroom_id: 'room-1' });
    expect(
      approveBody(1, 'reschedule', { exception: false, room: NO_ROOM }),
    ).toEqual({ version: 1, exception: false, classroom_id: null });
    expect(
      approveBody(1, 'reschedule', {
        exception: false,
        override: { codes: ['tutor_unavailable'], reason: 'Ok' },
      }),
    ).toEqual({
      version: 1,
      exception: false,
      override: { codes: ['tutor_unavailable'], reason: 'Ok' },
    });
  });

  it('checks the decision reason once trimmed', () => {
    expect(decisionReasonProblem('  ')).toBe('required');
    expect(decisionReasonProblem('x'.repeat(1001))).toBe('tooLong');
    expect(decisionReasonProblem(` ${'x'.repeat(1000)} `)).toBeNull();
  });

  it('knows the stale decisions and their messages', () => {
    const error = (code: string) => new ApiError({ status: 409, code });
    for (const code of [
      'change_request_not_pending',
      'change_request_session_started',
      'change_request_session_changed',
      'version_conflict',
    ]) {
      expect(isStaleChangeRequestError(error(code))).toBe(true);
    }
    expect(isStaleChangeRequestError(error('scheduling_conflict'))).toBe(false);
    expect(staleMessageKey(error('change_request_session_changed'))).toBe(
      'scheduling.changeRequest.stale.sessionChanged',
    );
    expect(staleMessageKey(error('version_conflict'))).toBe(
      'scheduling.changeRequest.stale.changed',
    );
  });
});

describe('/schedule/change-requests/:id', () => {
  it('shows the request and its policy outcome', async () => {
    stub();
    app = await open(PATH);

    expect(main().querySelector('h1')?.textContent).toBe(
      'Review change request',
    );
    expect(document.title).toContain('Review change request');
    const text = main().textContent ?? '';
    expect(text).toContain('Pending');
    expect(text).toContain('Cancellation');
    expect(text).toContain('Marta López (family)');
    expect(text).toContain('Ana López');
    expect(text).toContain('Física (Preparatoria)');
    expect(text).toContain('Carmen Ruiz');
    expect(text).toContain('Aula 1');
    expect(text).toContain('Tenemos un viaje familiar.');
    expect(text).toContain('Late — no makeup');
    expect(text).toContain('America/Mexico_City');
  });

  it('approves a cancellation and moves the focus to the decision', async () => {
    const api = stub(ADMIN, {
      [APPROVE]: () => jsonResponse(200, APPROVED_RESULT),
    });
    app = await open(PATH);

    await click(buttonByText(main(), 'Approve: cancel the session')!);
    await settle();

    expect(api.calls(APPROVE)[0]!.body).toEqual({
      version: 1,
      exception: false,
    });
    const text = main().textContent ?? '';
    expect(text).toContain('Approved as an exception: treated as excused.');
    expect(text).toContain('Excused — makeup until');
    expect(text).toContain(
      'Reason the requester sees: Enfermedad con justificante médico.',
    );
    expect(document.activeElement?.textContent).toBe('Decision');
    expect(
      main().querySelector(
        `a[href="/schedule/sessions/${APPROVED_RESULT.class_session.id}"]`,
      ),
    ).not.toBeNull();
  });

  it('approves as an exception with a required reason', async () => {
    const api = stub(ADMIN, {
      [APPROVE]: () => jsonResponse(200, APPROVED_RESULT),
    });
    app = await open(PATH);

    await click(buttonByText(main(), 'Approve as exception')!);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Treated as excused; the family will see this reason.',
    );
    // The focus starts on the reason, not on a button.
    expect(document.activeElement?.tagName).toBe('TEXTAREA');
    await click(buttonByText(dialog, 'Approve as exception')!);
    expect(dialog.textContent).toContain('Give a reason.');
    expect(api.calls(APPROVE)).toHaveLength(0);

    typeInto(dialog.querySelector('textarea')!, ' Enfermedad ');
    await click(buttonByText(dialog, 'Approve as exception')!);

    expect(api.calls(APPROVE)[0]!.body).toEqual({
      version: 1,
      exception: true,
      reason: 'Enfermedad',
    });
    expect(openDialog()).toBeNull();
  });

  it('a reschedule sends the chosen room; a conflict offers the override', async () => {
    const conflict = () =>
      problem(409, 'scheduling_conflict', { conflicts: SOFT_CONFLICTS });
    const api = stub(OWNER, {
      [REVIEW]: () => jsonResponse(200, PENDING_RESCHEDULE),
      [APPROVE]: [conflict, () => jsonResponse(200, APPROVED_RESULT)],
    });
    app = await open(PATH);

    const room = main().querySelector<HTMLSelectElement>('select')!;
    expect(room.options[0]!.textContent).toBe('Keep Aula 1');
    choose(room, '00000000-0000-4000-8000-0000000004d2');
    await click(buttonByText(main(), 'Approve: move the session')!);

    expect(api.calls(APPROVE)[0]!.body).toEqual({
      version: 1,
      exception: false,
      classroom_id: '00000000-0000-4000-8000-0000000004d2',
    });
    expect(main().textContent).toContain(
      `The session can’t be moved: ${SOFT_CONFLICTS.length} conflicts`,
    );
    await click(buttonByText(main(), 'Override and approve')!);
    const dialog = openDialog()!;
    typeInto(dialog.querySelector('textarea')!, 'Lo confirmó por teléfono');
    await click(buttonByText(dialog, 'Override and approve')!);

    const second = api.calls(APPROVE)[1]!.body as {
      classroom_id: string;
      override: { reason: string };
    };
    expect(second.classroom_id).toBe('00000000-0000-4000-8000-0000000004d2');
    expect(second.override.reason).toBe('Lo confirmó por teléfono');
  });

  it('an Administrator without schedule.override gets no override', async () => {
    stub(ADMIN, {
      [REVIEW]: () => jsonResponse(200, PENDING_RESCHEDULE),
      [APPROVE]: () =>
        problem(409, 'scheduling_conflict', { conflicts: SOFT_CONFLICTS }),
    });
    app = await open(PATH);

    await click(buttonByText(main(), 'Approve: move the session')!);

    expect(buttonByText(main(), 'Override and approve')).toBeUndefined();
  });

  it.each([
    [
      'change_request_not_pending',
      'This request was already decided or withdrawn. The page is updated.',
    ],
    [
      'change_request_session_started',
      'The session has started, so the request can’t be approved. The page is updated.',
    ],
    [
      'change_request_session_changed',
      'The session changed since the request was made (for example, it was cancelled), so the request can’t be approved. The page is updated.',
    ],
    [
      'version_conflict',
      'This request changed since you loaded it. The page is updated.',
    ],
  ])('%s shows a message and reloads the request', async (code, message) => {
    const api = stub(ADMIN, {
      [REVIEW]: [
        () => jsonResponse(200, PENDING_CANCELLATION),
        () => jsonResponse(200, REJECTED),
      ],
      [APPROVE]: () => problem(409, code),
    });
    app = await open(PATH);

    await click(buttonByText(main(), 'Approve: cancel the session')!);
    await settle();

    expect(alerts(main())).toContain(message);
    expect(api.calls(REVIEW).length).toBeGreaterThanOrEqual(2);
    expect(main().textContent).toContain('Decision');
  });

  it('a started request can only be rejected', async () => {
    stub(ADMIN, { [REVIEW]: () => jsonResponse(200, STARTED_CANCELLATION) });
    app = await open(PATH);

    expect(main().textContent).toContain('can no longer be approved');
    expect(buttonByText(main(), 'Approve: cancel the session')).toBeUndefined();
    expect(buttonByText(main(), 'Reject')).toBeDefined();
  });

  it('an excused request offers no exception', async () => {
    stub(ADMIN, {
      [REVIEW]: () =>
        jsonResponse(200, {
          ...PENDING_CANCELLATION,
          initiated_by: 'tutor',
          request_outcome: {
            classification: 'excused',
            makeup_allowed: true,
            makeup_until: '2031-02-06T16:00:00Z',
          },
        }),
    });
    app = await open(PATH);

    expect(buttonByText(main(), 'Approve as exception')).toBeUndefined();
  });

  it('rejects with a required reason and shows it', async () => {
    const api = stub(ADMIN, { [REJECT]: () => jsonResponse(200, REJECTED) });
    app = await open(PATH);

    const opener = buttonByText(main(), 'Reject')!;
    await click(opener);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('The requester will see this reason.');
    typeInto(dialog.querySelector('textarea')!, '  Sin sustituto  ');
    await click(buttonByText(dialog, 'Reject request')!);

    expect(api.calls(REJECT)[0]!.body).toEqual({
      version: 1,
      reason: 'Sin sustituto',
    });
    expect(openDialog()).toBeNull();
    const text = main().textContent ?? '';
    expect(text).toContain('Rejected: the session was not changed.');
    expect(text).toContain(
      'Reason the requester sees: La sesión ya tiene sustituto confirmado.',
    );
  });

  it('closing the reject dialog returns the focus to its button', async () => {
    stub();
    app = await open(PATH);

    const opener = buttonByText(main(), 'Reject')!;
    opener.focus();
    await click(opener);
    const dialog = openDialog()!;
    await click(buttonByText(dialog, 'Keep the request')!);
    await settle();

    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

describe('the inbox, the session page and notifications (G2, F3)', () => {
  const ITEM: ApprovalRequestItem = {
    id: '00000000-0000-4000-8000-0000000008c1',
    request_type: 'scheduling.change_request',
    status: 'pending',
    priority: 'high',
    requester: { id: 'r', display_name: 'Marta López' },
    assignee: null,
    created_at: '2031-01-06T18:00:00Z',
    resolved_at: null,
    resolved_by: null,
    reason: null,
    subject: {
      change_request_id: CHANGE_REQUEST_ID,
      kind: 'cancellation',
      initiated_by: 'family',
      student_name: 'Ana López',
      subject_name: 'Física',
      education_level_name: 'Preparatoria',
      starts_at: '2031-01-07T16:00:00Z',
      proposed_starts_at: null,
      classification: 'late',
      status: 'pending',
      class_session_id: APPROVED_EXCEPTION.session.id,
    },
    allowed_actions: [],
    version: 1,
  };

  it('the inbox shows a summary and "Review" instead of Approve and Reject', async () => {
    expect(changeRequestReviewLink(ITEM)).toBe(PATH);
    expect(itemReviewLink(ITEM)).toBe(PATH);
    expect(changeRequestReviewLink({ ...ITEM, status: 'approved' })).toBeNull();
    const t = i18n.getFixedT('en');
    expect(approvalSummary(ITEM, t, () => 'Tue 10:00')).toBe(
      'Cancellation · Ana López · Física · Tue 10:00 · Late',
    );
    stubApi({
      'GET /api/v1/auth/me': meAs(ADMIN),
      'GET /api/v1/approval-requests': () =>
        jsonResponse(200, { items: [ITEM], total: 1, limit: 10, offset: 0 }),
    });
    app = await open('/approvals');

    const link = main().querySelector<HTMLAnchorElement>(`a[href="${PATH}"]`);
    expect(link?.textContent).toBe('Review');
    expect(main().querySelectorAll('tbody button')).toHaveLength(0);
  });

  it('a route_required error names the review page', () => {
    const error = new ApiError({
      status: 409,
      code: 'change_request_review_route_required',
      problem: {
        type: 'about:blank',
        title: 'Conflict',
        status: 409,
        code: 'change_request_review_route_required',
        request_id: 'req-1',
        details: { review_path: PATH },
      },
    });
    expect(reviewLinkOf(error)).toBe(PATH);
  });

  it('the decision notifications read well, in the center zone', () => {
    const t = i18n.getFixedT('en');
    // 2026-03-08 is the Tijuana spring-forward day: 10:00 local is UTC-7.
    const tijuana = (instant: string) =>
      formatInstant(instant, {
        timeZone: 'America/Tijuana',
        language: 'en',
        style: 'dateTime',
      });
    const approved = notificationMessage(
      t,
      i18n,
      {
        notification_type: 'scheduling.change_request_approved',
        params: {
          change_request_id: CHANGE_REQUEST_ID,
          kind: 'cancellation',
          starts_at: '2026-03-08T17:00:00Z',
          new_starts_at: null,
          makeup_until: '2026-04-07T17:00:00Z',
          exception: true,
        },
      },
      tijuana,
    );
    expect(approved).toBe(
      `Your request to cancel the class on ${tijuana('2026-03-08T17:00:00Z')} was approved. A makeup class can be scheduled until ${tijuana('2026-04-07T17:00:00Z')}.`,
    );
    expect(tijuana('2026-03-08T17:00:00Z')).toContain('10:00');
    const moved = notificationMessage(
      t,
      i18n,
      {
        notification_type: 'scheduling.change_request_approved',
        params: {
          change_request_id: CHANGE_REQUEST_ID,
          kind: 'reschedule',
          starts_at: '2026-03-08T17:00:00Z',
          new_starts_at: '2026-03-09T18:00:00Z',
          makeup_until: null,
          exception: false,
        },
      },
      tijuana,
    );
    expect(moved).toContain('was approved: it is now on');
    const rejected = notificationMessage(
      t,
      i18n,
      {
        notification_type: 'scheduling.change_request_rejected',
        params: {
          change_request_id: CHANGE_REQUEST_ID,
          kind: 'reschedule',
          starts_at: '2026-03-08T17:00:00Z',
        },
      },
      tijuana,
    );
    expect(rejected).toContain('Your request to move the class on');
    expect(rejected).toContain('was rejected');
    // No family page yet (#62b): they only become read.
    expect(
      NOTIFICATION_LINKS['scheduling.change_request_approved'],
    ).toBeUndefined();
  });
});
