import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  noContent,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, labelled, openDialog, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { TuitionPlanDetail } from './api';
import {
  PLAN_ACTIVE_DETAIL,
  PLAN_ENDED_DETAIL,
  PLAN_PAUSED_DETAIL,
  PLAN_UNCHARGED_DETAIL,
  PLANS_PAGE,
} from './fixtures';

// #374 H1 (E): the plan page: the summary, the upcoming periods with their
// states (a future period inside a pause shows paused; a withdrawal's
// charged period shows charged; a due period without a price shows why),
// the charged periods with their charge status, the pauses, read-only
// without billing.manage, and each action dialog (success with the
// version, 409 version_conflict → the shared message and a reload, 422 on
// its field), Delete only when nothing is charged, and 404.

const ME = 'GET /api/v1/auth/me';
const detail = (id: string) => `GET /api/v1/billing/tuition-plans/${id}`;
const patch = (id: string) => `PATCH /api/v1/billing/tuition-plans/${id}`;
const remove = (id: string) => `DELETE /api/v1/billing/tuition-plans/${id}`;
const pauses = (id: string) =>
  `POST /api/v1/billing/tuition-plans/${id}/pauses`;
const resume = (id: string) =>
  `POST /api/v1/billing/tuition-plans/${id}/resume`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'guardian.view', 'billing.view', 'billing.manage'],
);
const VIEWER = user(['finance_staff'], ['billing.view']);

const PLANS = [
  PLAN_ACTIVE_DETAIL,
  PLAN_PAUSED_DETAIL,
  PLAN_ENDED_DETAIL,
  PLAN_UNCHARGED_DETAIL,
];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { as = FINANCE }: { as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    'GET /api/v1/billing/tuition-plans': () => jsonResponse(200, PLANS_PAGE),
    ...Object.fromEntries(
      PLANS.map((plan) => [detail(plan.id), () => jsonResponse(200, plan)]),
    ),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const dialog = () => openDialog()!;
const select = (root: ParentNode, label: string) =>
  labelled<HTMLSelectElement>(root, label);
const optionValues = (field: HTMLSelectElement) =>
  [...field.options].map((option) => [option.value, option.textContent]);
const actions = () =>
  [...main().querySelectorAll('h1 + div button')].map((b) => b.textContent);
const terms = () =>
  Object.fromEntries(
    [...main().querySelectorAll('dl dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling?.textContent,
    ]),
  );
const upcoming = (start: string) =>
  main().querySelector(`[data-upcoming-period="${start}"]`)!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const errorOf = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => document.getElementById(id)?.textContent)
    .filter(Boolean)
    .join('|');

async function renderPlan(plan: TuitionPlanDetail) {
  app = await renderApp(`/billing/tuition-plans/${plan.id}`);
  await settleUntil(() => main().querySelector('dl'));
}

async function open(name: string) {
  await click(buttonNamed(main(), name));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-20T18:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the plan page (E1-E3)', () => {
  it('shows the summary, the periods and the pauses', async () => {
    stub();
    await renderPlan(PLAN_ACTIVE_DETAIL);

    expect(h1()).toBe('Tuition plan of Ana López');
    expect(document.title).toBe('Tuition plan of Ana López · Classora');
    expect(terms()).toEqual({
      Student: 'Ana López',
      Family: 'Familia López',
      Price: 'Individual class · per period · monthly, Secundaria · Álgebra',
      Frequency: 'Monthly',
      'Current amount': '$1,500.00',
      Starts: 'Oct 1, 2026',
      Ends: 'Open-ended',
      Due: '5 days after the period starts',
      Status: 'Active',
      Group: 'Individual',
    });
    const price = [...main().querySelectorAll('dd a')][0];
    expect(price?.getAttribute('href')).toBe(
      `/billing/prices/${PLAN_ACTIVE_DETAIL.service_price.id}`,
    );
    expect(cells(upcoming('2026-10-01'))).toEqual([
      'Oct 1, 2026 – Oct 31, 2026',
      'Charged',
    ]);
    expect(cells(upcoming('2026-11-01'))[1]).toBe('Upcoming');
    // A future period inside a pause is reported not_due: shown paused.
    expect(cells(upcoming('2027-01-01'))[1]).toBe('Paused');
    expect(cells(upcoming('2027-02-01'))[1]).toBe('Upcoming');
    expect(
      cells(main().querySelector('[data-recent-period="2026-10-01"]')!),
    ).toEqual(['Oct 1, 2026 – Oct 31, 2026', '$1,500.00', 'Pending']);
    expect(
      [...main().querySelectorAll('[data-pause-id]')].map(
        (li) => li.textContent,
      ),
    ).toEqual(['Jan 1, 2027 – Jan 31, 2027']);
    expect(actions()).toEqual(['Pause', 'End', 'Edit due days']);
  });

  it('shows a due period without a price and offers Delete when nothing is charged', async () => {
    stub();
    await renderPlan(PLAN_UNCHARGED_DETAIL);

    expect(cells(upcoming('2026-10-01'))[1]).toBe(
      'Due — not chargedNo price for the period',
    );
    expect(main().textContent).toContain('No period has been charged yet.');
    expect(main().textContent).toContain('No pauses.');
    expect(actions()).toEqual(['Pause', 'End', 'Edit due days', 'Delete']);
  });

  it('shows a paused plan with Resume and its open pause', async () => {
    stub();
    await renderPlan(PLAN_PAUSED_DETAIL);

    expect(terms().Group).toBe('Álgebra sábados');
    expect(terms().Due).toBe('On the day the period starts');
    expect(cells(upcoming('2026-10-19'))[1]).toBe('Paused');
    expect(
      [...main().querySelectorAll('[data-pause-id]')].map(
        (li) => li.textContent,
      ),
    ).toEqual(['Oct 19, 2026 – Until resumed']);
    expect(
      [...main().querySelectorAll('[data-recent-period]')].map(
        (row) => cells(row)[2],
      ),
    ).toEqual(['Paid', 'Overdue']);
    expect(actions()).toEqual(['Resume', 'End', 'Edit due days']);
  });

  it('shows an ended plan’s charged withdrawal period as charged, without actions', async () => {
    stub();
    await renderPlan(PLAN_ENDED_DETAIL);

    expect(terms().Status).toBe('Ended');
    expect(cells(upcoming('2026-10-19'))[1]).toBe('Charged');
    expect(actions()).toEqual([]);
  });

  it('is read-only without billing.manage', async () => {
    stub({}, { as: VIEWER });
    await renderPlan(PLAN_UNCHARGED_DETAIL);

    expect(actions()).toEqual([]);
    expect(main().querySelector('dd a')).toBeNull();
    expect(terms().Price).toBe(
      'Individual class · per period · monthly, Secundaria · Álgebra',
    );
  });

  it('says not found for an unknown plan', async () => {
    stub({
      [detail('a0000000-0000-4000-8000-0000000000ff')]: () =>
        problemWith(404, 'not_found'),
    });
    app = await renderApp(
      '/billing/tuition-plans/a0000000-0000-4000-8000-0000000000ff',
    );
    await settleUntil(() => h1() === 'Tuition plan not found');

    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to tuition plans',
    );
    expect(back?.getAttribute('href')).toBe('/billing/tuition-plans');
  });
});

describe('the actions (E4)', () => {
  it('pauses from the next period until resumed, with the version', async () => {
    const paused: TuitionPlanDetail = {
      ...PLAN_ACTIVE_DETAIL,
      version: 4,
      status: 'active',
    };
    const api = stub({
      [pauses(PLAN_ACTIVE_DETAIL.id)]: () => jsonResponse(201, paused),
    });
    await renderPlan(PLAN_ACTIVE_DETAIL);
    await open('Pause');

    // Not the charged October nor the paused January; ends stop before it.
    expect(optionValues(select(dialog(), 'First paused period'))).toEqual([
      ['2026-11-01', 'Nov 1, 2026'],
      ['2026-12-01', 'Dec 1, 2026'],
      ['2027-02-01', 'Feb 1, 2027'],
    ]);
    expect(optionValues(select(dialog(), 'Until'))).toEqual([
      ['', 'Until resumed'],
      ['2026-11-30', 'Nov 30, 2026'],
      ['2026-12-31', 'Dec 31, 2026'],
    ]);
    await click(buttonNamed(dialog(), 'Pause'));

    expect(api.calls(pauses(PLAN_ACTIVE_DETAIL.id))[0]?.body).toEqual({
      version: 3,
      starts_on: '2026-11-01',
    });
    expect(openDialog()).toBeNull();
    expect(statusText()).toContain('Plan paused.');
  });

  it('sends a pause end when one is chosen and puts a 422 on its field', async () => {
    const api = stub({
      [pauses(PLAN_ACTIVE_DETAIL.id)]: () =>
        problemWith(422, 'tuition_pause_invalid', {
          errors: [
            { path: ['body', 'ends_on'], code: 'tuition_pause_invalid' },
          ],
        }),
    });
    await renderPlan(PLAN_ACTIVE_DETAIL);
    await open('Pause');
    setFieldValue(select(dialog(), 'First paused period'), '2026-12-01');
    expect(optionValues(select(dialog(), 'Until'))).toEqual([
      ['', 'Until resumed'],
      ['2026-12-31', 'Dec 31, 2026'],
    ]);
    setFieldValue(select(dialog(), 'Until'), '2026-12-31');
    await click(buttonNamed(dialog(), 'Pause'));

    expect(api.calls(pauses(PLAN_ACTIVE_DETAIL.id))[0]?.body).toEqual({
      version: 3,
      starts_on: '2026-12-01',
      ends_on: '2026-12-31',
    });
    expect(select(dialog(), 'Until').getAttribute('aria-invalid')).toBe('true');
    expect(errorOf(select(dialog(), 'Until'))).toContain(
      'A pause starts on an uncharged period',
    );
  });

  it('shows a 409 version_conflict and reloads the plan', async () => {
    const api = stub({
      [pauses(PLAN_ACTIVE_DETAIL.id)]: () =>
        problemWith(409, 'version_conflict'),
    });
    await renderPlan(PLAN_ACTIVE_DETAIL);
    const reads = api.calls(detail(PLAN_ACTIVE_DETAIL.id)).length;
    await open('Pause');
    await click(buttonNamed(dialog(), 'Pause'));
    await settleUntil(
      () => api.calls(detail(PLAN_ACTIVE_DETAIL.id)).length > reads,
    );

    expect(alerts(dialog()).join()).toContain(
      'Someone else changed this record.',
    );
    expect(openDialog()).not.toBeNull();
  });

  it('resumes the open pause from a chosen period', async () => {
    const api = stub({
      [resume(PLAN_PAUSED_DETAIL.id)]: () =>
        jsonResponse(200, { ...PLAN_PAUSED_DETAIL, version: 3 }),
    });
    await renderPlan(PLAN_PAUSED_DETAIL);
    await open('Resume');

    expect(dialog().textContent).toContain('Paused since Oct 19, 2026.');
    expect(optionValues(select(dialog(), 'Charge again from'))).toEqual([
      ['2026-10-19', 'Oct 19, 2026'],
      ['2026-11-02', 'Nov 2, 2026'],
      ['2026-11-16', 'Nov 16, 2026'],
    ]);
    // The first period not paused today is suggested.
    expect(select(dialog(), 'Charge again from').value).toBe('2026-11-02');
    await click(buttonNamed(dialog(), 'Resume'));

    expect(api.calls(resume(PLAN_PAUSED_DETAIL.id))[0]?.body).toEqual({
      version: 2,
      resume_on: '2026-11-02',
    });
    expect(statusText()).toContain('Plan resumed.');
  });

  it('puts tuition_resume_invalid on the resume date', async () => {
    stub({
      [resume(PLAN_PAUSED_DETAIL.id)]: () =>
        problemWith(422, 'tuition_resume_invalid'),
    });
    await renderPlan(PLAN_PAUSED_DETAIL);
    await open('Resume');
    await click(buttonNamed(dialog(), 'Resume'));

    expect(errorOf(select(dialog(), 'Charge again from'))).toContain(
      'Resume on the start of a period',
    );
  });

  it('ends after a chosen period, or removes the end date', async () => {
    const saved = { ...PLAN_UNCHARGED_DETAIL, version: 2 };
    const api = stub({
      [patch(PLAN_UNCHARGED_DETAIL.id)]: () => jsonResponse(200, saved),
      // The reload after the save reads the new version.
      [detail(PLAN_UNCHARGED_DETAIL.id)]: [
        () => jsonResponse(200, PLAN_UNCHARGED_DETAIL),
        () => jsonResponse(200, saved),
      ],
    });
    await renderPlan(PLAN_UNCHARGED_DETAIL);
    await open('End');

    expect(dialog().textContent).toContain('It ends on Jan 31, 2027 today.');
    expect(optionValues(select(dialog(), 'Last day'))).toEqual([
      ['2026-10-31', 'Oct 31, 2026'],
      ['2026-11-30', 'Nov 30, 2026'],
      ['2026-12-31', 'Dec 31, 2026'],
      ['none', 'Remove end date (open-ended)'],
    ]);
    setFieldValue(select(dialog(), 'Last day'), '2026-11-30');
    await click(buttonNamed(dialog(), 'Save'));
    expect(api.calls(patch(PLAN_UNCHARGED_DETAIL.id))[0]?.body).toEqual({
      version: 1,
      ends_on: '2026-11-30',
    });
    expect(statusText()).toContain('End date saved.');

    await settleUntil(() => main().querySelector('h1 + div button'));
    await open('End');
    setFieldValue(select(dialog(), 'Last day'), 'none');
    await click(buttonNamed(dialog(), 'Save'));
    expect(api.calls(patch(PLAN_UNCHARGED_DETAIL.id))[1]?.body).toEqual({
      version: 2,
      ends_on: null,
    });
  });

  it('offers no end before the last charged period and maps tuition_plan_ends_on_invalid', async () => {
    stub({
      [patch(PLAN_ACTIVE_DETAIL.id)]: () =>
        problemWith(422, 'tuition_plan_ends_on_invalid', {
          errors: [
            { path: ['body', 'ends_on'], code: 'tuition_plan_ends_on_invalid' },
          ],
        }),
    });
    await renderPlan(PLAN_ACTIVE_DETAIL);
    await open('End');

    expect(dialog().textContent).toContain('It is open-ended today.');
    expect(optionValues(select(dialog(), 'Last day'))[0]).toEqual([
      '2026-10-31',
      'Oct 31, 2026',
    ]);
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(select(dialog(), 'Last day'))).toContain(
      'The plan must end on the last day of a period',
    );
  });

  it('edits the due days', async () => {
    const saved = { ...PLAN_ACTIVE_DETAIL, version: 4, due_offset_days: 10 };
    const api = stub({
      [patch(PLAN_ACTIVE_DETAIL.id)]: () => jsonResponse(200, saved),
      [detail(PLAN_ACTIVE_DETAIL.id)]: [
        () => jsonResponse(200, PLAN_ACTIVE_DETAIL),
        () => jsonResponse(200, saved),
      ],
    });
    await renderPlan(PLAN_ACTIVE_DETAIL);
    await open('Edit due days');

    const days = labelled<HTMLInputElement>(
      dialog(),
      'Due days after each period starts',
    );
    expect(days.value).toBe('5');
    setFieldValue(days, '61');
    await click(buttonNamed(dialog(), 'Save'));
    expect(errorOf(days)).toContain('Enter whole days from 0 to 60.');
    expect(api.calls(patch(PLAN_ACTIVE_DETAIL.id))).toHaveLength(0);

    setFieldValue(days, '10');
    await click(buttonNamed(dialog(), 'Save'));
    expect(api.calls(patch(PLAN_ACTIVE_DETAIL.id))[0]?.body).toEqual({
      version: 3,
      due_offset_days: 10,
    });
    await settleUntil(() => terms().Due === '10 days after the period starts');
    expect(statusText()).toContain('Due days saved.');
  });

  it('deletes a plan without charges and goes back to the list', async () => {
    let deleteUrl: URL | undefined;
    const api = stub({
      [remove(PLAN_UNCHARGED_DETAIL.id)]: (request) => {
        deleteUrl = new URL(request.url);
        return noContent();
      },
    });
    await renderPlan(PLAN_UNCHARGED_DETAIL);
    await click(buttonNamed(main(), 'Delete'));
    await settleUntil(() => openDialog());

    expect(dialog().textContent).toContain(
      'The plan of Ana López (Álgebra · any level · Individual · Monthly) and its pauses will be deleted.',
    );
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(
      () => app!.router.state.location.pathname === '/billing/tuition-plans',
    );

    expect(api.calls(remove(PLAN_UNCHARGED_DETAIL.id))).toHaveLength(1);
    expect(deleteUrl?.searchParams.get('version')).toBe('1');
  });

  it('keeps the confirmation open on a refused delete', async () => {
    stub({
      [remove(PLAN_UNCHARGED_DETAIL.id)]: () =>
        problemWith(409, 'tuition_plan_has_charges'),
    });
    await renderPlan(PLAN_UNCHARGED_DETAIL);
    await click(buttonNamed(main(), 'Delete'));
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Delete'));

    expect(alerts(dialog()).join()).toContain(
      "This plan has charged periods and can't be deleted.",
    );
  });
});
