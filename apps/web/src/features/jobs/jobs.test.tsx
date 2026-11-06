import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click, labelled, openDialog, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { LABELLED_TASKS, taskLabelKey, type FailedJob } from './api';
import {
  BACKFILLED,
  CHARGE_RUN,
  JOBS,
  manyJobs,
  RETRIED,
  TASKS,
  UNKNOWN_TASK,
} from './fixtures';

// M8-11 (#128) G: the /jobs page. Filters in the URL and their checks (G2),
// the list, paging and its states (G3, G6), the retry with its confirmation
// and refusals (G4), access (G1) and the task labels (G3, G7).

const LIST = 'GET /api/v1/jobs';
const TASK_LIST = 'GET /api/v1/jobs/tasks';
const RETRY = `POST /api/v1/jobs/${CHARGE_RUN.id}/retry`;
const ADMIN: SessionUserBody = {
  ...TEST_USER,
  roles: ['administrator'],
  permissions: ['user.view', 'audit.view', 'jobs.manage'],
};
// The fixtures' times in America/Mexico_City (UTC−6 in November).
const CHARGE_TIME = 'Nov 4, 2026, 12:30 PM';

const page = (items: FailedJob[], next: string | null = null) =>
  jsonResponse(200, { items, next_cursor: next });

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const liveText = () =>
  main().querySelector('p[aria-live="polite"]')?.textContent ?? '';
const rows = () =>
  [...main().querySelectorAll('tbody > tr')].map(
    (row) => row.textContent ?? '',
  );
const listCalls = (api: ApiStub) =>
  api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === '/api/v1/jobs');
const lastListQuery = (api: ApiStub) =>
  Object.fromEntries(listCalls(api).at(-1)!.searchParams);

function stub(routes: Record<string, Responder | Responder[]> = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(ADMIN),
    [TASK_LIST]: () => jsonResponse(200, { items: TASKS }),
    [LIST]: () => page(JOBS),
    ...routes,
  });
}

async function open(path = '/jobs') {
  app = await renderApp(path);
  await settleUntil(() => main()?.querySelector('h1'));
  await settle();
}

async function submitFilters() {
  const form = main().querySelector('form')!;
  await click(buttonNamed(form, 'Apply filters'));
}

async function confirmRetryOf(label: string) {
  await click(buttonNamed(main(), label));
  await settleUntil(() => openDialog());
  await click(buttonNamed(openDialog()!, 'Retry'));
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

describe('the list (G3)', () => {
  it('shows each job with its task, status, attempts, error, time and retries', async () => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    expect(main().querySelector('h1')!.textContent).toBe('Failed jobs');
    expect(document.title).toContain('Failed jobs');
    expect(main().textContent).toContain(
      'Errors on single items are retried automatically',
    );
    const [charge, backfilled, retried, unknown] = rows();
    expect(charge).toContain('Session charges');
    expect(charge).toContain('billing:post_session_charges');
    expect(charge).toContain('Failed');
    expect(charge).toContain('RuntimeError');
    expect(charge).toContain(CHARGE_TIME);
    expect(charge).toContain('None');
    expect(backfilled).toContain('Payroll period opening');
    expect(backfilled).toContain('Unknown');
    expect(retried).toContain('IntegrityError · SQLSTATE 23505');
    expect(retried).toContain('1, last Oct 30, 2026, 7:00 AM');
    // A task with no label shows its code; the app code follows the class.
    expect(unknown).toContain('legacy:renamed_task');
    expect(unknown).toContain('ChargeRunError · charge_run_failed');
    expect(liveText()).toBe('4 jobs shown');
    expect(lastListQuery(api)).toEqual({ limit: '25' });
  });

  it('never renders arguments, and offers Retry only for retryable jobs', async () => {
    // A response carrying an extra field must still never show it.
    const withArgs: FailedJob = Object.assign(
      { args: { student: 'Ana López' } },
      CHARGE_RUN,
    );
    stub({ [LIST]: () => page([withArgs, UNKNOWN_TASK]) });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    expect(main().textContent).not.toContain('Ana López');
    expect(main().textContent).not.toContain('args');
    const names = [...main().querySelectorAll('tbody button')].map((button) =>
      button.getAttribute('aria-label'),
    );
    expect(names).toEqual([`Retry Session charges, failed ${CHARGE_TIME}`]);
  });

  it('loads more with the cursor and hides the button on the last page', async () => {
    const api = stub({
      [LIST]: [
        () => page(manyJobs(25), 'cursor-2'),
        () => page(manyJobs(3, 900)),
      ],
    });
    await open();
    await settleUntil(() => rows().length === 25);
    expect(liveText()).toBe('25 jobs shown');

    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => rows().length === 28);

    expect(lastListQuery(api)).toEqual({ limit: '25', cursor: 'cursor-2' });
    expect(liveText()).toBe('28 jobs shown');
    expect(() => buttonNamed(main(), 'Load more')).toThrow();
  });
});

describe('the filters (G2)', () => {
  it('reads every filter from the URL and sends center-local day bounds', async () => {
    const api = stub();
    await open(
      '/jobs?task=billing:post_session_charges&status=any&from=2026-11-01&to=2026-11-04',
    );
    await settleUntil(() => main().querySelector('tbody'));

    expect(lastListQuery(api)).toEqual({
      task: 'billing:post_session_charges',
      status: 'any',
      // 00:00 in Mexico City (UTC−6), and the start of the day after "To".
      from: '2026-11-01T06:00:00.000Z',
      to: '2026-11-05T06:00:00.000Z',
      limit: '25',
    });
    expect(labelled<HTMLSelectElement>(main(), 'Task').value).toBe(
      'billing:post_session_charges',
    );
    expect(labelled<HTMLSelectElement>(main(), 'Status').value).toBe('any');
    expect(labelled<HTMLInputElement>(main(), 'From').value).toBe('2026-11-01');
    expect(labelled<HTMLInputElement>(main(), 'To').value).toBe('2026-11-04');
    buttonNamed(main(), 'Clear filters');
  });

  it('applies the form to the URL, offering the tasks by label and every status', async () => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));
    expect(() => buttonNamed(main(), 'Clear filters')).toThrow();
    const taskOptions = [
      ...labelled<HTMLSelectElement>(main(), 'Task').options,
    ].map((option) => option.textContent);
    expect(taskOptions).toEqual([
      'All',
      'Package expiry',
      'Session charges',
      'Payroll period opening',
    ]);
    const statusOptions = [
      ...labelled<HTMLSelectElement>(main(), 'Status').options,
    ].map((option) => option.textContent);
    expect(statusOptions).toEqual([
      'Failed',
      'All',
      'Queued',
      'Running',
      'Succeeded',
      'Cancelled',
      'Aborted',
    ]);

    setFieldValue(labelled(main(), 'Task'), 'billing:expire_packages');
    setFieldValue(labelled(main(), 'Status'), 'succeeded');
    await submitFilters();
    await settle();

    expect(app!.location()).toBe(
      '/jobs?task=billing%3Aexpire_packages&status=succeeded',
    );
    expect(lastListQuery(api)).toEqual({
      task: 'billing:expire_packages',
      status: 'succeeded',
      limit: '25',
    });

    await click(buttonNamed(main(), 'Clear filters'));
    await settle();
    expect(app!.location()).toBe('/jobs');
  });

  it('blocks the request when To is before From', async () => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));
    const before = listCalls(api).length;

    setFieldValue(labelled(main(), 'From'), '2026-11-04');
    setFieldValue(labelled(main(), 'To'), '2026-11-01');
    await submitFilters();
    await settle();

    expect(main().textContent).toContain(
      'The end date must be on or after the start date.',
    );
    expect(listCalls(api)).toHaveLength(before);
    expect(app!.location()).toBe('/jobs');
    expect(document.activeElement?.getAttribute('aria-invalid')).toBe('true');
  });

  it('sends no request for an invalid URL', async () => {
    const api = stub();
    await open('/jobs?from=2026-11-04&to=2026-11-01');

    expect(main().textContent).toContain('Fix the filters to see the jobs.');
    expect(listCalls(api)).toHaveLength(0);
  });
});

describe('retry (G4)', () => {
  it('confirms, then replaces the row with the response and announces it', async () => {
    const queued: FailedJob = {
      ...CHARGE_RUN,
      status: 'todo',
      attempts: 2,
      retried_count: 1,
      last_retried_at: '2026-11-04T19:00:00Z',
      retryable: false,
    };
    const api = stub({ [RETRY]: () => jsonResponse(202, queued) });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(
      buttonNamed(main(), `Retry Session charges, failed ${CHARGE_TIME}`),
    );
    await settleUntil(() => openDialog());
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Retry Session charges?');
    expect(dialog.textContent).toContain(
      "It doesn't duplicate charges or records.",
    );
    expect(api.calls(RETRY)).toHaveLength(0);
    await click(buttonNamed(dialog, 'Retry'));
    await settleUntil(() => !openDialog());

    expect(api.calls(RETRY)).toHaveLength(1);
    expect(liveText()).toBe('Session charges is queued to retry.');
    const [charge] = rows();
    expect(charge).toContain('Queued');
    expect(charge).toContain('1, last Nov 4, 2026, 1:00 PM');
    expect(
      [...main().querySelectorAll('tbody button')].map((b) =>
        b.getAttribute('aria-label'),
      ),
    ).not.toContain(`Retry Session charges, failed ${CHARGE_TIME}`);
  });

  it('cancelling sends nothing', async () => {
    const api = stub({ [RETRY]: () => jsonResponse(202, CHARGE_RUN) });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(
      buttonNamed(main(), `Retry Session charges, failed ${CHARGE_TIME}`),
    );
    await settleUntil(() => openDialog());
    await click(buttonNamed(openDialog()!, 'Cancel'));
    await settle();

    expect(openDialog()).toBeNull();
    expect(api.calls(RETRY)).toHaveLength(0);
  });

  it.each([
    ['job_already_queued', 409, 'A queued run will already cover this job.'],
    ['job_not_failed', 409, 'This job is no longer failed.'],
    ['job_not_retryable', 409, "This job's task can't be retried."],
  ] as const)(
    '%s shows its own message and reloads the list',
    async (code, status, message) => {
      const api = stub({
        [RETRY]: () => problem(status, code),
        [LIST]: [() => page(JOBS), () => page([BACKFILLED, RETRIED])],
      });
      await open();
      await settleUntil(() => main().querySelector('tbody'));
      const before = listCalls(api).length;

      await confirmRetryOf(`Retry Session charges, failed ${CHARGE_TIME}`);
      await settleUntil(() => listCalls(api).length > before);
      await settle();

      expect(main().querySelector('[role="alert"]')?.textContent).toBe(message);
      expect(main().textContent).not.toContain(
        'Server detail that must never reach the UI',
      );
      expect(rows()).toHaveLength(2);
    },
  );

  it('a 404 reloads the list too', async () => {
    const api = stub({
      [RETRY]: () => problem(404, 'not_found'),
      [LIST]: [() => page(JOBS), () => page([])],
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await confirmRetryOf(`Retry Session charges, failed ${CHARGE_TIME}`);
    await settleUntil(() =>
      main().textContent?.includes('No failed jobs match these filters.'),
    );

    expect(api.calls(RETRY)).toHaveLength(1);
    expect(main().querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe('states (G6)', () => {
  it('shows the empty state', async () => {
    stub({ [LIST]: () => page([]) });
    await open();
    await settleUntil(() =>
      main().textContent?.includes('No failed jobs match these filters.'),
    );
  });

  it('restarts from the first page after invalid_cursor', async () => {
    const api = stub({
      [LIST]: [
        () => page(manyJobs(25), 'cursor-2'),
        () => problem(400, 'invalid_cursor'),
        () => page(manyJobs(2)),
      ],
    });
    await open();
    await settleUntil(() => rows().length === 25);

    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => main().textContent?.includes('The list changed'));
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => rows().length === 2);

    expect(lastListQuery(api)).toEqual({ limit: '25' });
  });

  it('shows the shared alert with a retry on another error', async () => {
    stub({ [LIST]: [() => problem(500, 'internal_error'), () => page(JOBS)] });
    await open();
    await settleUntil(() => main().querySelector('[role="alert"]'));

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => rows().length === JOBS.length);
  });
});

describe('access (G1) and labels (G7)', () => {
  it('shows the Forbidden page and no nav item without jobs.manage', async () => {
    const api = stubApi({ 'GET /api/v1/auth/me': meAs(TEST_USER) });
    app = await renderApp('/jobs');
    await settleUntil(() =>
      main()?.textContent?.includes("You don't have access to this page"),
    );

    expect(listCalls(api)).toHaveLength(0);
    expect(app.container.querySelector('a[href="/jobs"]')).toBeNull();
  });

  it('shows the nav item to a jobs.manage holder', async () => {
    stub();
    await open();

    expect(app!.container.querySelector('a[href="/jobs"]')?.textContent).toBe(
      'Failed jobs',
    );
  });

  it('has an es and en label for every classified task, without a raw colon', () => {
    // The API's RETRYABLE_TASKS | NON_RETRYABLE_TASKS (tests/unit/test_job_rules.py).
    expect(LABELLED_TASKS).toHaveLength(21);
    for (const task of LABELLED_TASKS) {
      const key = taskLabelKey(task);
      expect(key).not.toContain(':');
      for (const lng of ['es', 'en']) {
        expect(i18n.exists(key, { lng }), `${lng} ${key}`).toBe(true);
      }
    }
  });
});
