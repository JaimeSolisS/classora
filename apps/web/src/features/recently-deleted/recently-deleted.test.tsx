import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
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
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { DeletedRecord } from './api';

// M8-12 (#129) D: the /recently-deleted page. The list and its states (D2,
// D5), the restore with its message, link and refusals (D3), and access
// (D1).

const LIST = 'GET /api/v1/deleted-records';
const TUTOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['tutor'],
  permissions: ['student.view', 'tutor.view'],
};

const PLAN: DeletedRecord = {
  kind: 'learning_plan',
  id: '01900000-0000-7000-8000-000000000001',
  version: 3,
  student: {
    id: '01900000-0000-7000-8000-0000000000a1',
    display_name: 'Ana Ruiz',
  },
  title: 'Plan de lectura',
  reference_date: '2031-03-03',
  deleted_at: '2031-03-05T18:00:00Z',
  restorable_until: '2031-04-04T18:00:00Z',
};
const NOTE: DeletedRecord = {
  kind: 'session_note',
  id: '01900000-0000-7000-8000-000000000002',
  version: 2,
  student: {
    id: '01900000-0000-7000-8000-0000000000a2',
    display_name: 'Luis Paz',
  },
  title: null,
  reference_date: '2031-03-04',
  deleted_at: '2031-03-04T18:00:00Z',
  restorable_until: '2031-04-03T18:00:00Z',
};
const restorePath = (record: DeletedRecord) =>
  `POST /api/v1/deleted-records/${record.kind}/${record.id}/restore`;

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const liveText = () =>
  main().querySelector('p[aria-live="polite"]')?.textContent ?? '';
const alertText = () =>
  main().querySelector('p[role="alert"]')?.textContent ?? '';
const rows = () =>
  [...main().querySelectorAll('tbody > tr')].map(
    (row) => row.textContent ?? '',
  );

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = TUTOR,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: () => jsonResponse(200, { items: [PLAN, NOTE], has_more: false }),
    ...routes,
  });
}

async function open() {
  app = await renderApp('/recently-deleted');
  await settleUntil(() => main()?.querySelector('h1'));
  await settle();
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

describe('the list (D2, D5)', () => {
  it('shows each record with its kind, name, student and dates', async () => {
    stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    expect(main().querySelector('h1')!.textContent).toBe('Recently deleted');
    expect(document.title).toContain('Recently deleted');
    expect(main().textContent).toContain('You can restore it for 30 days');
    const [plan, note] = rows();
    expect(plan).toContain('Learning plan');
    expect(plan).toContain('Plan de lectura');
    expect(plan).toContain('Ana Ruiz');
    expect(plan).toContain('Deleted on Mar 5, 2031');
    expect(plan).toContain('Available until Apr 4, 2031');
    // A record without a title: its kind and date.
    expect(note).toContain('Class note of Mar 4, 2031');
    expect(note).toContain('Luis Paz');
    expect(main().textContent).not.toContain('100 most recently');
    expect(
      buttonNamed(main(), 'Restore Learning plan: Plan de lectura'),
    ).toBeTruthy();
  });

  it('says when only the 100 most recent are shown', async () => {
    stub({
      [LIST]: () => jsonResponse(200, { items: [PLAN], has_more: true }),
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    expect(main().textContent).toContain(
      'Only the 100 most recently deleted are shown.',
    );
  });

  it('has an empty state', async () => {
    stub({ [LIST]: () => jsonResponse(200, { items: [], has_more: false }) });
    await open();
    await settleUntil(() => main().textContent?.includes('no records'));

    expect(main().textContent).toContain(
      'You have no records deleted in the last 30 days.',
    );
    expect(main().querySelector('table')).toBeNull();
  });

  it('shows a skeleton while loading, then the shared alert with a retry', async () => {
    const api = stub({
      [LIST]: [
        () => problem(500, 'internal_error'),
        () => jsonResponse(200, { items: [PLAN], has_more: false }),
      ],
    });
    await open();
    await settleUntil(() => main().querySelector('[role="alert"]'));

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST)).toHaveLength(2);
    expect(rows()).toHaveLength(1);
  });
});

describe('restoring (D3)', () => {
  it('restores without a confirmation, removes the row and offers Open', async () => {
    const api = stub({
      [restorePath(PLAN)]: () =>
        jsonResponse(200, { kind: PLAN.kind, id: PLAN.id, version: 4 }),
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(buttonNamed(main(), 'Restore Learning plan: Plan de lectura'));
    await settleUntil(() => liveText().includes('was restored'));

    expect(api.calls(restorePath(PLAN))[0]!.body).toEqual({ version: 3 });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(liveText()).toContain('Plan de lectura was restored.');
    const link = main().querySelector<HTMLAnchorElement>(
      'p[aria-live="polite"] a',
    )!;
    expect(link.textContent).toBe('Open');
    expect(link.getAttribute('href')).toBe(`/learning-plans/${PLAN.id}`);
    expect(link.getAttribute('aria-label')).toBe('Open Plan de lectura');
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toContain('Luis Paz');
    expect(document.activeElement).toBe(main().querySelector('h1'));
  });

  it('gives a restored class note no Open link (it has no page of its own)', async () => {
    stub({
      [restorePath(NOTE)]: () =>
        jsonResponse(200, { kind: NOTE.kind, id: NOTE.id, version: 3 }),
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(
      buttonNamed(main(), 'Restore Class note: Class note of Mar 4, 2031'),
    );
    await settleUntil(() => liveText().includes('was restored'));

    expect(main().querySelector('p[aria-live="polite"] a')).toBeNull();
  });

  it.each([
    [404, 'not_found', 'This record can no longer be restored.'],
    [
      409,
      'deleted_record_restore_conflict',
      'A live record already takes its place',
    ],
  ])(
    'a %s %s shows its own message and refreshes the list',
    async (status, code, text) => {
      const api = stub({ [restorePath(PLAN)]: () => problem(status, code) });
      await open();
      await settleUntil(() => main().querySelector('tbody'));

      await click(
        buttonNamed(main(), 'Restore Learning plan: Plan de lectura'),
      );
      await settleUntil(() => alertText().length > 0);

      expect(alertText()).toContain(text);
      await settleUntil(() => api.calls(LIST).length === 2);
    },
  );

  it("a 403 shows the kind's own message", async () => {
    const api = stub({
      [restorePath(PLAN)]: () => problem(403, 'plan_read_only'),
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(buttonNamed(main(), 'Restore Learning plan: Plan de lectura'));
    await settleUntil(() => alertText().length > 0);

    expect(alertText()).not.toBe('');
    expect(alertText()).not.toContain('This record can no longer be restored');
    expect(api.calls(LIST)).toHaveLength(1);
    expect(rows()).toHaveLength(2);
  });
});

describe('access (D1)', () => {
  it('lets a learning_plan.manage holder in', async () => {
    stub(
      {},
      {
        ...TEST_USER,
        roles: ['academic_coordinator'],
        permissions: ['student.view', 'learning_plan.manage'],
      },
    );
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    expect(main().querySelector('h1')!.textContent).toBe('Recently deleted');
  });

  it('keeps Finance out', async () => {
    const api = stub(
      {},
      {
        ...TEST_USER,
        roles: ['finance_staff'],
        permissions: ['student.view', 'schedule.view'],
      },
    );
    await open();

    expect(main().querySelector('h1')!.textContent).not.toBe(
      'Recently deleted',
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });
});
