import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { applyLanguage, i18n } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  reviewLinkOf,
  sessionRequestReviewLink,
} from '@/features/approvals/summary';
import type { ApprovalRequestItem } from '@/features/approvals/api';
import {
  NOTIFICATION_LINKS,
  notificationMessage,
} from '@/features/notifications/messages';
import {
  CONFIRMED_REQUEST,
  REJECTED_REQUEST,
} from '@/features/family-portal/requests/fixtures';
import {
  meAs,
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
  settle,
  type RenderedApp,
} from '@/test/render-app';
import {
  CLASSROOMS,
  SOFT_CONFLICTS,
  TUTOR_SUGGESTIONS,
  TUTORS,
} from '../create/fixtures';
import {
  CONFIRMED,
  CONFIRMED_REVIEW,
  PENDING_REVIEW,
  REJECTED_REVIEW,
  REQUEST_ID,
  STARTED_REVIEW,
} from './fixtures';
import {
  confirmBody,
  isStaleRequestError,
  rejectionReasonProblem,
  requestSuggestionParams,
  staleMessageKey,
} from './rules';

// #60 H1-H3: the staff review page (summary, suggested tutors, confirm with
// the tutor required, the 409 messages and reload, the conflict panel and
// override, the reject dialog), the inbox "Review" link, the family's
// confirmed and rejected cards and the two decision notifications.

const REVIEW = `GET /api/v1/session-requests/${REQUEST_ID}/review`;
const CONFIRM = `POST /api/v1/session-requests/${REQUEST_ID}/confirm`;
const REJECT = `POST /api/v1/session-requests/${REQUEST_ID}/reject`;
const SUGGEST = 'GET /api/v1/tutor-suggestions';

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
    [REVIEW]: () => jsonResponse(200, PENDING_REVIEW),
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
    'GET /api/v1/classrooms': () =>
      jsonResponse(200, {
        items: CLASSROOMS.map((room) => ({
          id: room.id,
          name: room.name,
          capacity: 10,
          is_active: true,
          version: 1,
          availability: [],
        })),
        total: CLASSROOMS.length,
        limit: 100,
        offset: 0,
      }),
    [SUGGEST]: () => jsonResponse(200, TUTOR_SUGGESTIONS),
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

const tutorSelect = () =>
  main().querySelector<HTMLSelectElement>('form select')!;

/** The lazy route needs a few more turns to load, then its queries (the
 * request, then the tutors and classrooms of the confirm panel). */
async function open(path: string): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  for (let i = 0; i < 60; i += 1) {
    const page = rendered.container.querySelector('main');
    const ready =
      page?.querySelector('h1') &&
      !page.querySelector('[aria-busy="true"]') &&
      (page.querySelector('form select') ||
        page.textContent?.includes('Decision') ||
        page.textContent?.includes('can no longer be confirmed') ||
        page.querySelector('table') ||
        page.querySelector('ul'));
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

describe('review rules', () => {
  it('builds the confirm body: tutor, version, room, deadline and override', () => {
    expect(
      confirmBody(
        { tutorId: 't-1', deadlineDate: '', deadlineTime: '', classroomId: '' },
        1,
        'America/Mexico_City',
      ),
    ).toEqual({ version: 1, tutor_user_id: 't-1' });
    expect(
      confirmBody(
        {
          tutorId: 't-1',
          classroomId: 'room-1',
          deadlineDate: '2031-01-05',
          deadlineTime: '09:30',
        },
        2,
        'America/Mexico_City',
        { codes: ['notice_window'], reason: 'Urgente' },
      ),
    ).toEqual({
      version: 2,
      tutor_user_id: 't-1',
      classroom_id: 'room-1',
      respond_by: '2031-01-05T15:30:00.000Z',
      override: { codes: ['notice_window'], reason: 'Urgente' },
    });
  });

  it('converts the deadline across a Tijuana DST change', () => {
    const body = confirmBody(
      { tutorId: 't', deadlineDate: '2026-03-09', deadlineTime: '10:00' },
      1,
      'America/Tijuana',
    );
    expect(body.respond_by).toBe('2026-03-09T17:00:00.000Z');
  });

  it('asks for suggestions for the request slot (and the chosen room)', () => {
    expect(requestSuggestionParams(PENDING_REVIEW)).toEqual({
      subject_id: PENDING_REVIEW.subject.id,
      starts_at: PENDING_REVIEW.starts_at,
      ends_at: PENDING_REVIEW.ends_at,
    });
    expect(requestSuggestionParams(PENDING_REVIEW, 'room').classroom_id).toBe(
      'room',
    );
  });

  it('checks the rejection reason once trimmed', () => {
    expect(rejectionReasonProblem('  ')).toBe('required');
    expect(rejectionReasonProblem('x'.repeat(1001))).toBe('tooLong');
    expect(rejectionReasonProblem(` ${'x'.repeat(1000)} `)).toBeNull();
  });

  it('knows the stale decisions and their messages', () => {
    const error = (code: string) => new ApiError({ status: 409, code });
    expect(isStaleRequestError(error('session_request_not_pending'))).toBe(
      true,
    );
    expect(isStaleRequestError(error('session_request_started'))).toBe(true);
    expect(isStaleRequestError(error('version_conflict'))).toBe(true);
    expect(isStaleRequestError(error('scheduling_conflict'))).toBe(false);
    expect(staleMessageKey(error('session_request_started'))).toBe(
      'scheduling.requestReview.stale.started',
    );
    expect(staleMessageKey(error('session_request_not_pending'))).toBe(
      'scheduling.requestReview.stale.notPending',
    );
    expect(staleMessageKey(error('version_conflict'))).toBe(
      'scheduling.requestReview.stale.changed',
    );
  });
});

describe('/schedule/requests/:id', () => {
  it('shows the request and suggests tutors for its slot', async () => {
    const api = stub();
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      'Review session request',
    );
    expect(document.title).toContain('Review session request');
    const text = main().textContent ?? '';
    expect(text).toContain('Pending');
    expect(text).toContain('Ana López');
    expect(text).toContain('Álgebra (Secundaria)');
    expect(text).toContain('60 min');
    expect(text).toContain('Marta López');
    expect(text).toContain('Prefiere con la misma tutora de siempre.');
    expect(text).toContain('America/Mexico_City');

    await click(buttonByText(main(), 'Suggest tutors')!);
    const [query] = api.calls(SUGGEST);
    expect(query).toBeDefined();
    await click(buttonNamed(main(), 'Choose Diego Torres'));
    expect(tutorSelect().value).toBe(TUTORS[1]!.id);
  });

  it('needs a tutor, then confirms and links the offered session', async () => {
    const api = stub(ADMIN, {
      [CONFIRM]: () => jsonResponse(200, CONFIRMED),
    });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    await click(buttonByText(main(), 'Confirm request')!);
    expect(api.calls(CONFIRM)).toHaveLength(0);
    expect(main().textContent).toContain('Choose a tutor.');

    choose(tutorSelect(), TUTORS[0]!.id);
    const room = main().querySelectorAll<HTMLSelectElement>('form select')[1]!;
    choose(room, CLASSROOMS[0]!.id);
    await click(buttonByText(main(), 'Confirm request')!);

    expect(api.calls(CONFIRM)[0]!.body).toEqual({
      version: 1,
      tutor_user_id: TUTORS[0]!.id,
      classroom_id: CLASSROOMS[0]!.id,
    });
    const text = main().textContent ?? '';
    expect(text).toContain(
      'Request confirmed. The session was offered to Carmen Ruiz.',
    );
    const link = main().querySelector<HTMLAnchorElement>(
      `a[href="/schedule/sessions/${CONFIRMED.class_session.id}"]`,
    );
    expect(link?.textContent).toBe('Open the session');
    expect(buttonByText(main(), 'Reject request')).toBeUndefined();
  });

  it('a conflict shows the panel; the Owner overrides', async () => {
    const conflict = () =>
      problem(409, 'scheduling_conflict', { conflicts: SOFT_CONFLICTS });
    const api = stub(OWNER, {
      [CONFIRM]: [conflict, () => jsonResponse(200, CONFIRMED)],
    });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    choose(tutorSelect(), TUTORS[0]!.id);
    await click(buttonByText(main(), 'Confirm request')!);
    expect(main().textContent).toContain(
      `The request can’t be confirmed: ${SOFT_CONFLICTS.length} conflicts`,
    );
    await click(buttonByText(main(), 'Override and confirm')!);
    const dialog = openDialog()!;
    typeInto(dialog.querySelector('textarea')!, 'Acordado con la familia');
    await click(buttonByText(dialog, 'Override and confirm')!);

    const second = api.calls(CONFIRM)[1]!.body as {
      override: { codes: string[]; reason: string };
    };
    expect(second.override.reason).toBe('Acordado con la familia');
    expect(main().textContent).toContain('Request confirmed.');
  });

  it('an Administrator without schedule.override gets no override', async () => {
    stub(ADMIN, {
      [CONFIRM]: () =>
        problem(409, 'scheduling_conflict', { conflicts: SOFT_CONFLICTS }),
    });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    choose(tutorSelect(), TUTORS[0]!.id);
    await click(buttonByText(main(), 'Confirm request')!);

    expect(buttonByText(main(), 'Override and confirm')).toBeUndefined();
  });

  it.each([
    [
      'session_request_not_pending',
      'This request was already decided or withdrawn. The page is updated.',
    ],
    [
      'session_request_started',
      'The requested time has started, so the request can’t be confirmed. The page is updated.',
    ],
    [
      'version_conflict',
      'This request changed since you loaded it. The page is updated.',
    ],
  ])('%s shows a message and reloads the request', async (code, message) => {
    const api = stub(ADMIN, {
      [REVIEW]: [
        () => jsonResponse(200, PENDING_REVIEW),
        () => jsonResponse(200, CONFIRMED_REVIEW),
      ],
      [CONFIRM]: () => problem(409, code),
    });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    choose(tutorSelect(), TUTORS[0]!.id);
    await click(buttonByText(main(), 'Confirm request')!);

    expect(alerts(main())).toContain(message);
    expect(api.calls(REVIEW).length).toBeGreaterThanOrEqual(2);
    expect(main().textContent).toContain('Decision');
  });

  it('a started request can only be rejected', async () => {
    stub(ADMIN, { [REVIEW]: () => jsonResponse(200, STARTED_REVIEW) });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    const text = main().textContent ?? '';
    expect(text).toContain('can no longer be confirmed');
    expect(buttonByText(main(), 'Confirm request')).toBeUndefined();
    expect(buttonByText(main(), 'Reject request')).toBeDefined();
  });

  it('rejects with a required reason and shows it', async () => {
    const api = stub(ADMIN, {
      [REJECT]: () => jsonResponse(200, REJECTED_REVIEW),
    });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    await click(buttonByText(main(), 'Reject request')!);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('The family will see this reason.');
    await click(buttonByText(dialog, 'Reject request')!);
    expect(dialog.textContent).toContain('Give a reason.');
    expect(api.calls(REJECT)).toHaveLength(0);

    typeInto(dialog.querySelector('textarea')!, '  No hay tutores  ');
    await click(buttonByText(dialog, 'Reject request')!);

    expect(api.calls(REJECT)[0]!.body).toEqual({
      version: 1,
      reason: 'No hay tutores',
    });
    expect(openDialog()).toBeNull();
    const text = main().textContent ?? '';
    expect(text).toContain('Rejected.');
    expect(text).toContain(
      'Reason the family sees: No hay tutores disponibles ese día.',
    );
  });

  it('a too-long reason from the server goes on the field', async () => {
    stub(ADMIN, {
      [REJECT]: () =>
        problem(422, 'validation_error', {
          errors: [
            {
              path: ['body', 'reason'],
              code: 'string_too_long',
              message: '',
            },
          ],
        }),
    });
    app = await open(`/schedule/requests/${REQUEST_ID}`);

    await click(buttonByText(main(), 'Reject request')!);
    const dialog = openDialog()!;
    typeInto(dialog.querySelector('textarea')!, 'Motivo');
    await click(buttonByText(dialog, 'Reject request')!);

    expect(dialog.textContent).toContain('Use at most 1000 characters.');
  });
});

describe('the inbox and the family (H2, G2, F3)', () => {
  const ITEM: ApprovalRequestItem = {
    id: '00000000-0000-4000-8000-0000000008a1',
    request_type: 'scheduling.session_request',
    status: 'pending',
    priority: 'normal',
    requester: { id: 'r', display_name: 'Marta López' },
    assignee: null,
    created_at: '2031-01-02T18:00:00Z',
    resolved_at: null,
    resolved_by: null,
    reason: null,
    subject: {
      student_name: 'Ana López',
      subject_name: 'Álgebra',
      education_level_name: 'Secundaria',
      starts_at: '2031-01-06T16:00:00Z',
      ends_at: '2031-01-06T17:00:00Z',
      duration_minutes: 60,
      requested_by: 'Marta López',
      note: null,
      status: 'pending',
      class_session_id: null,
      session_request_id: REQUEST_ID,
    },
    // #60: decided on the review page, so the API lists no actions.
    allowed_actions: [],
    version: 1,
  };

  it('the inbox shows "Review" instead of Approve and Reject', async () => {
    expect(sessionRequestReviewLink(ITEM)).toBe(
      `/schedule/requests/${REQUEST_ID}`,
    );
    expect(
      sessionRequestReviewLink({ ...ITEM, status: 'approved' }),
    ).toBeNull();
    stubApi({
      'GET /api/v1/auth/me': meAs(ADMIN),
      'GET /api/v1/approval-requests': () =>
        jsonResponse(200, { items: [ITEM], total: 1, limit: 10, offset: 0 }),
    });
    app = await open('/approvals');

    const link = main().querySelector<HTMLAnchorElement>(
      `a[href="/schedule/requests/${REQUEST_ID}"]`,
    );
    expect(link?.textContent).toBe('Review');
    expect(main().querySelectorAll('tbody button')).toHaveLength(0);
  });

  it('a route_required error names the review page', () => {
    const error = new ApiError({
      status: 409,
      code: 'session_request_review_route_required',
      problem: {
        type: 'about:blank',
        title: 'Conflict',
        status: 409,
        code: 'session_request_review_route_required',
        request_id: 'req-1',
        details: { review_path: `/schedule/requests/${REQUEST_ID}` },
      },
    });
    expect(reviewLinkOf(error)).toBe(`/schedule/requests/${REQUEST_ID}`);
    expect(reviewLinkOf(new ApiError({ status: 409, code: 'x' }))).toBeNull();
  });

  it('My requests says confirmed and shows the rejection reason', async () => {
    stubApi({
      'GET /api/v1/auth/me': meAs({
        ...TEST_USER,
        roles: ['guardian'],
        permissions: [],
      }),
      'GET /api/v1/session-requests': () =>
        jsonResponse(200, {
          items: [CONFIRMED_REQUEST, REJECTED_REQUEST],
          total: 2,
          limit: 100,
          offset: 0,
        }),
    });
    app = await open('/requests');

    const text = main().textContent ?? '';
    expect(text).toContain('Confirmed — the center scheduled this session.');
    expect(text).toContain('No hay tutores disponibles ese día.');
    expect(buttonByText(main(), 'Withdraw')).toBeUndefined();
  });

  it('the decision notifications link to My requests', () => {
    const t = i18n.getFixedT('en');
    for (const type of [
      'scheduling.session_request_confirmed',
      'scheduling.session_request_rejected',
    ]) {
      expect(NOTIFICATION_LINKS[type]?.({}, { userId: 'u' })).toBe('/requests');
      const text = notificationMessage(
        t,
        i18n,
        {
          notification_type: type,
          params: {
            session_request_id: REQUEST_ID,
            student_name: 'Ana López',
            subject_name: 'Álgebra',
            starts_at: '2031-01-06T16:00:00Z',
          },
        },
        () => 'Jan 6, 10:00 AM',
      );
      expect(text).toContain('Ana López (Álgebra, Jan 6, 10:00 AM)');
    }
  });
});
