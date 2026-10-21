import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  NOTIFICATION_LINKS,
  notificationMessage,
} from '@/features/notifications/messages';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import { reasonRequired, type ApprovalRequestItem } from './api';
import { actionLabelKey, approvalSummary, reassignmentLink } from './summary';

// #52 H3, F3: the reassignment inbox item (summary, session link, "Mark
// resolved" / "Dismiss", reassignment_still_needed in the dialog) and the
// three offer notifications (messages with formatted instants, links); the
// series variants of both (#233 G3, E1).

const ID = '00000000-0000-4000-8000-0000000008c1';
const SESSION_ID = '00000000-0000-4000-8000-0000000004e1';
const LIST = 'GET /api/v1/approval-requests';
const APPROVE = `POST /api/v1/approval-requests/${ID}/approve`;

const ITEM: ApprovalRequestItem = {
  id: ID,
  request_type: 'scheduling.offer_reassignment',
  status: 'pending',
  priority: 'high',
  requester: null,
  assignee: null,
  created_at: '2026-10-21T15:30:00Z',
  resolved_at: null,
  resolved_by: null,
  reason: null,
  subject: {
    class_session_id: SESSION_ID,
    subject_name: 'Álgebra',
    starts_at: '2031-01-06T16:00:00Z',
    tutor_name: 'Carmen Ruiz',
    outcome: 'declined',
    decline_note: 'Tengo otra clase.',
    session_state: 'unassigned',
  },
  allowed_actions: ['approve', 'reject'],
  version: 1,
};

const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['schedule.view', 'schedule.assign'],
};

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function stub(routes: Record<string, Responder | Responder[]> = {}) {
  return stubApi({
    'GET /api/v1/auth/me': meAs(COORDINATOR),
    [LIST]: () =>
      jsonResponse(200, { items: [ITEM], total: 1, limit: 10, offset: 0 }),
    ...routes,
  });
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

describe('the reassignment item (H3)', () => {
  it('summarises a declined or expired offer and links to the session', () => {
    const t = i18n.getFixedT('en');
    expect(approvalSummary(ITEM, t, () => 'Jan 6, 10:00 AM')).toBe(
      'Declined by Carmen Ruiz · Álgebra · Jan 6, 10:00 AM',
    );
    expect(
      approvalSummary(
        { ...ITEM, subject: { ...ITEM.subject, outcome: 'expired' } },
        t,
        () => 'Jan 6, 10:00 AM',
      ),
    ).toBe('Expired, offered to Carmen Ruiz · Álgebra · Jan 6, 10:00 AM');
    expect(reassignmentLink(ITEM)).toBe(`/schedule/sessions/${SESSION_ID}`);
    expect(actionLabelKey(ITEM.request_type, 'approve')).toBe(
      'approvals.actions.markResolved',
    );
    expect(actionLabelKey(ITEM.request_type, 'reject')).toBe(
      'approvals.actions.dismiss',
    );
    expect(reasonRequired(ITEM.request_type, 'reject')).toBe(true);
  });

  it('a coordinator sees it in Approvals with the link, the note and the labels', async () => {
    stub();
    app = await renderApp('/approvals');

    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toContain('Approvals');
    const text = main().textContent ?? '';
    expect(text).toContain('Session to reassign');
    expect(text).toContain(
      'Declined by Carmen Ruiz · Álgebra · Jan 6, 2031, 10:00 AM',
    );
    expect(text).toContain("Tutor's note: Tengo otra clase.");
    const link = main().querySelector<HTMLAnchorElement>(
      `a[href="/schedule/sessions/${SESSION_ID}"]`,
    );
    expect(link?.textContent).toBe('Open the session');
    expect(
      [...main().querySelectorAll('tbody button')].map((b) => b.textContent),
    ).toEqual(['Mark resolved', 'Dismiss']);
  });

  it('reassignment_still_needed shows in the dialog', async () => {
    stub({ [APPROVE]: () => problem(409, 'reassignment_still_needed') });
    app = await renderApp('/approvals');

    const button = [...main().querySelectorAll('tbody button')].find(
      (b) => b.textContent === 'Mark resolved',
    ) as HTMLButtonElement;
    await click(button);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Mark resolved: Declined by');
    await click(buttonNamed(dialog, 'Mark resolved'));

    expect(alerts(openDialog()!)[0]).toContain(
      'The session still has no tutor. Offer it to a tutor first.',
    );
  });
});

const SERIES_ITEM: ApprovalRequestItem = {
  ...ITEM,
  subject: {
    ...ITEM.subject,
    class_series_id: '00000000-0000-4000-8000-0000000006c1',
    from_date: '2031-01-06',
    occurrence_count: 24,
  },
};

describe('the series reassignment item (#233 G3)', () => {
  it('summarises the weekly series with its from-date and session count', () => {
    const t = i18n.getFixedT('en');
    const date = (localDate: string) => `<${localDate}>`;
    expect(approvalSummary(SERIES_ITEM, t, () => 'Jan 6, 10:00 AM', date)).toBe(
      'Declined by Carmen Ruiz · Álgebra · weekly series from <2031-01-06> (24 sessions)',
    );
    expect(
      approvalSummary(
        {
          ...SERIES_ITEM,
          subject: { ...SERIES_ITEM.subject, outcome: 'expired' },
        },
        i18n.getFixedT('es'),
        () => '',
        date,
      ),
    ).toBe(
      'Vencida, ofrecida a Carmen Ruiz · Álgebra · serie semanal desde el <2031-01-06> (24 sesiones)',
    );
    expect(reassignmentLink(SERIES_ITEM)).toBe(
      `/schedule/sessions/${SESSION_ID}`,
    );
  });

  it('a series offer that covered no session has no session link', () => {
    const empty: ApprovalRequestItem = {
      ...SERIES_ITEM,
      subject: {
        ...SERIES_ITEM.subject,
        class_session_id: null,
        starts_at: null,
        session_state: null,
        occurrence_count: 0,
      },
    };
    expect(reassignmentLink(empty)).toBeNull();
    expect(approvalSummary(empty, i18n.getFixedT('en'))).toBe(
      'Declined by Carmen Ruiz · Álgebra · weekly series from 2031-01-06 (0 sessions)',
    );
  });

  it('the inbox row shows the series summary', async () => {
    stub({
      [LIST]: () =>
        jsonResponse(200, {
          items: [SERIES_ITEM],
          total: 1,
          limit: 10,
          offset: 0,
        }),
    });
    app = await renderApp('/approvals');

    expect(main().textContent).toContain(
      'Declined by Carmen Ruiz · Álgebra · weekly series from Jan 6, 2031 (24 sessions)',
    );
  });
});

describe('the offer notifications (F3)', () => {
  it('a series offer uses the series messages and links (#233 E1)', () => {
    const message = (type: string, params: object) =>
      notificationMessage(
        i18n.getFixedT('en'),
        i18n,
        { notification_type: type, params: params as never },
        (instant) => `<${instant}>`,
      );
    const series = {
      class_series_id: '00000000-0000-4000-8000-0000000006c1',
      from_date: '2031-01-06',
      occurrence_count: 24,
    };
    expect(
      message('scheduling.offer_received', {
        offer_id: 'o',
        subject_name: 'Álgebra',
        starts_at: '2031-01-06T16:00:00Z',
        respond_by: '2031-01-05T10:00:00Z',
        ...series,
      }),
    ).toBe(
      'Weekly series offered: Álgebra, from Jan 6, 2031. Answer by <2031-01-05T10:00:00Z>.',
    );
    const closed = {
      offer_id: 'o',
      class_session_id: null,
      subject_name: 'Álgebra',
      starts_at: '2031-01-06T16:00:00Z',
      tutor_name: 'Carmen Ruiz',
      ...series,
    };
    expect(message('scheduling.offer_declined', closed)).toBe(
      'Carmen Ruiz declined the weekly series Álgebra from Jan 6, 2031. Offer it to another tutor.',
    );
    expect(message('scheduling.offer_expired', closed)).toContain(
      'expired without an answer',
    );
    const me = { userId: 'u' };
    expect(NOTIFICATION_LINKS['scheduling.offer_declined']?.(closed, me)).toBe(
      '/approvals',
    );
    expect(
      NOTIFICATION_LINKS['scheduling.offer_expired']?.(
        { ...closed, class_session_id: SESSION_ID },
        me,
      ),
    ).toBe(`/schedule/sessions/${SESSION_ID}`);
  });

  const format = (instant: string) => `<${instant}>`;

  it('formats the instants and never shows a note', () => {
    const message = (lng: string, type: string, params: object) =>
      notificationMessage(
        i18n.getFixedT(lng),
        i18n,
        { notification_type: type, params: params as never },
        format,
      );
    expect(
      message('en', 'scheduling.offer_received', {
        offer_id: 'o',
        subject_name: 'Álgebra',
        starts_at: '2031-01-06T16:00:00Z',
        respond_by: '2031-01-05T10:00:00Z',
      }),
    ).toBe(
      'You have a new session offer: Álgebra, <2031-01-06T16:00:00Z>. Answer by <2031-01-05T10:00:00Z>.',
    );
    const closed = {
      offer_id: 'o',
      class_session_id: SESSION_ID,
      subject_name: 'Álgebra',
      starts_at: '2031-01-06T16:00:00Z',
      tutor_name: 'Carmen Ruiz',
    };
    expect(message('es', 'scheduling.offer_declined', closed)).toBe(
      'Carmen Ruiz rechazó Álgebra, <2031-01-06T16:00:00Z>. Ofrécela a otro tutor.',
    );
    expect(message('en', 'scheduling.offer_expired', closed)).toContain(
      'expired without an answer',
    );
  });

  it('links the tutor to My offers and the staff to the session', () => {
    const me = { userId: 'u' };
    expect(NOTIFICATION_LINKS['scheduling.offer_received']?.({}, me)).toBe(
      '/offers',
    );
    for (const type of [
      'scheduling.offer_declined',
      'scheduling.offer_expired',
    ]) {
      expect(
        NOTIFICATION_LINKS[type]?.({ class_session_id: SESSION_ID }, me),
      ).toBe(`/schedule/sessions/${SESSION_ID}`);
    }
  });
});
