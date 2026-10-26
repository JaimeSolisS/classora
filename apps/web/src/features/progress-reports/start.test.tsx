import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import { click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  APPROVED_PAGE,
  APPROVED_PAGE_NO_CREATE,
  COORDINATOR,
  DRAFT,
  FINANCE,
  GUARDIAN,
  MATHS,
  PHYSICS,
  REPORT_ID,
  STUDENT_ID,
  TUTOR,
} from './fixtures';

// #315 B, A1, H1 (start): the access (a Coordinator who isn't a tutor, a
// guardian and Finance send nothing), can_create false, the default On
// demand body, weekly (snapped to the Monday, This/Last week, a future week
// refused), monthly, the 366-day rule, the subjects from AM2, the
// navigation to the editor with its notice focused, and every B6 row with
// its focus (the choices are kept).

const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const HINTS = `GET /api/v1/students/${STUDENT_ID}/progress-reports`;
const CREATE = 'POST /api/v1/progress-reports';
const REPORT = `GET /api/v1/progress-reports/${REPORT_ID}`;
const PAGE = `/students/${STUDENT_ID}/progress-reports/new`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');
const focused = () => document.activeElement;
const field = <
  T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
>(
  label: string,
) => labelled<T>(main(), label);
const button = (name: string) => buttonNamed(main(), name);
const radio = (value: string) =>
  main().querySelector<HTMLInputElement>(
    `input[type="radio"][value="${value}"]`,
  )!;
const describedText = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent)
    .join(' ');

function stub(routes: Routes = {}, as = TUTOR) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [HINTS]: () => jsonResponse(200, APPROVED_PAGE),
    [CREATE]: () => jsonResponse(201, DRAFT),
    [REPORT]: () => jsonResponse(200, DRAFT),
    ...routes,
  });
}

async function open(routes: Routes = {}, as = TUTOR) {
  stub(routes, as);
  app = await renderApp(PAGE);
  await settleUntil(() => main().querySelector('form') ?? heading());
}

async function start() {
  await click(button('Start report'));
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  // Wednesday 4 Nov 2026, 12:00 in the center (America/Mexico_City).
  vi.setSystemTime(new Date('2026-11-04T18:00:00Z'));
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('access (A1, B1)', () => {
  it.each([
    ['a Coordinator who is not a tutor', COORDINATOR],
    ['a guardian', GUARDIAN],
    ['Finance', FINANCE],
  ] as const)('%s gets access-denied and sends nothing', async (_who, as) => {
    stub({}, as);
    app = await renderApp(PAGE);
    await settleUntil(() => heading());
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(HINTS)).toHaveLength(0);
    expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
  });

  it('can_create false: the message and no form', async () => {
    await open({ [HINTS]: () => jsonResponse(200, APPROVED_PAGE_NO_CREATE) });
    await settleUntil(() => text().includes('currently teach'));
    expect(text()).toContain(
      'You can start a report only for a student you currently teach.',
    );
    expect(main().querySelector('form')).toBeNull();
  });

  it('asks R10 for one item (the create hints)', async () => {
    const urls: URL[] = [];
    await open({
      [HINTS]: (request) => {
        urls.push(new URL(request.url));
        return jsonResponse(200, APPROVED_PAGE);
      },
    });
    expect(urls[0]!.searchParams.get('limit')).toBe('1');
    expect(heading()!.textContent).toBe('New progress report');
    expect(text()).toContain('José');
  });

  it('a 404 on the student shows the not-found page', async () => {
    stub({ [STUDENT_ROUTE]: () => problemWith(404, 'not_found') });
    app = await renderApp(PAGE);
    await settleUntil(() => text().includes('Page not found'));
  });
});

describe('the form (B2-B5)', () => {
  it('On demand by default: the last 7 days, all subjects', async () => {
    await open();
    expect(radio('on_demand').checked).toBe(true);
    expect(field<HTMLInputElement>('From').value).toBe('2026-10-29');
    expect(field<HTMLInputElement>('To').value).toBe('2026-11-04');
    expect(text()).toContain(
      "The period and subject can't change after you start the report.",
    );
    await start();
    expect(api.calls(CREATE)[0]!.body).toEqual({
      student_id: STUDENT_ID,
      kind: 'on_demand',
      period_start: '2026-10-29',
      period_end: '2026-11-04',
    });
    // Replaces the URL with the editor, whose notice takes focus.
    await settleUntil(
      () => app!.location() === `/progress-reports/${REPORT_ID}`,
    );
    await settleUntil(() =>
      focused()?.textContent?.startsWith('Report started.'),
    );
    expect(focused()?.getAttribute('role')).toBe('status');
    expect(app!.router.state.historyAction).toBe('REPLACE');
  });

  it("today is the center's, not the device's or UTC's", async () => {
    // 03:00Z on 5 Nov is still 4 Nov, 21:00 in the center.
    vi.setSystemTime(new Date('2026-11-05T03:00:00Z'));
    await open();
    expect(field<HTMLInputElement>('To').value).toBe('2026-11-04');
    expect(field<HTMLInputElement>('From').value).toBe('2026-10-29');
  });

  it('the subjects are the ones the tutor teaches, as returned', async () => {
    await open();
    const subject = field<HTMLSelectElement>('Subject');
    expect([...subject.options].map((option) => option.textContent)).toEqual([
      'All subjects',
      'Física',
      'Matemáticas',
    ]);
    setFieldValue(subject, MATHS.id);
    await start();
    expect(api.calls(CREATE)[0]!.body).toMatchObject({ subject_id: MATHS.id });
  });

  it('labels each subject with its level when GET /subjects knows it', async () => {
    await open({
      'GET /api/v1/subjects': () =>
        jsonResponse(200, {
          items: [
            {
              id: PHYSICS.id,
              name: 'Física',
              education_level_name: 'Secundaria',
              is_active: true,
            },
          ],
          total: 1,
          limit: 100,
          offset: 0,
        }),
    });
    await settleUntil(() =>
      [...field<HTMLSelectElement>('Subject').options].some(
        (option) => option.textContent === 'Física (Secundaria)',
      ),
    );
    expect(
      [...field<HTMLSelectElement>('Subject').options].map(
        (option) => option.textContent,
      ),
    ).toEqual(['All subjects', 'Física (Secundaria)', 'Matemáticas']);
  });

  it('weekly: any day snaps to its Monday; This week / Last week', async () => {
    await open();
    await click(radio('weekly'));
    const day = field<HTMLInputElement>('Any day in the week');
    expect(day.value).toBe('2026-11-04');
    expect(text()).toContain('Week of Nov 2, 2026 – Nov 8, 2026');
    await click(button('Last week'));
    expect(day.value).toBe('2026-10-26');
    await click(button('This week'));
    expect(day.value).toBe('2026-11-02');
    setFieldValue(day, '2026-11-05');
    await start();
    const body = api.calls(CREATE)[0]!.body as Record<string, unknown>;
    expect(body).toEqual({
      student_id: STUDENT_ID,
      kind: 'weekly',
      period_start: '2026-11-02',
    });
    expect('period_end' in body).toBe(false);
  });

  it("weekly: a week that hasn't started is refused on the field", async () => {
    await open();
    await click(radio('weekly'));
    const day = field<HTMLInputElement>('Any day in the week');
    setFieldValue(day, '2026-11-09');
    await start();
    await settleUntil(() => focused() === day);
    expect(day.getAttribute('aria-invalid')).toBe('true');
    expect(describedText(day)).toContain("The week hasn't started yet.");
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('monthly: this month and the 12 before it, sent as the 1st', async () => {
    await open();
    await click(radio('monthly'));
    const month = field<HTMLSelectElement>('Month');
    expect(month.options).toHaveLength(13);
    expect(month.options[0]!.textContent).toBe('November 2026');
    expect(month.options[12]!.textContent).toBe('November 2025');
    setFieldValue(month, '2026-10-01');
    await start();
    expect(api.calls(CREATE)[0]!.body).toEqual({
      student_id: STUDENT_ID,
      kind: 'monthly',
      period_start: '2026-10-01',
    });
  });

  it('on demand: 367 days refused on To, From after today on From', async () => {
    await open();
    const from = field<HTMLInputElement>('From');
    const to = field<HTMLInputElement>('To');
    setFieldValue(from, '2025-01-01');
    setFieldValue(to, '2026-01-02');
    await start();
    await settleUntil(() => focused() === to);
    expect(describedText(to)).toContain('at most 366 days');
    setFieldValue(from, '2026-11-05');
    setFieldValue(to, '2026-11-06');
    await start();
    await settleUntil(() => focused() === from);
    expect(describedText(from)).toContain("can't start after today");
    expect(api.calls(CREATE)).toHaveLength(0);
    // 366 days inclusive goes out.
    setFieldValue(from, '2025-01-01');
    setFieldValue(to, '2026-01-01');
    await start();
    expect(api.calls(CREATE)).toHaveLength(1);
  });
});

describe('refusals (B6)', () => {
  it('period_invalid lands on the period control, choices kept', async () => {
    await open({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'period_end'], code: 'period_invalid' }],
        }),
    });
    await start();
    const to = field<HTMLInputElement>('To');
    await settleUntil(() => focused() === to);
    expect(describedText(to)).toContain('Choose a valid period');
    expect(to.value).toBe('2026-11-04');
  });

  it('period_in_future lands on the week', async () => {
    await open({
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'period_start'], code: 'period_in_future' },
          ],
        }),
    });
    await click(radio('weekly'));
    await start();
    const day = field<HTMLInputElement>('Any day in the week');
    await settleUntil(() => focused() === day);
    expect(describedText(day)).toContain("can't start after today");
    expect(radio('weekly').checked).toBe(true);
  });

  it('progress_report_exists: the alert and "Open it"', async () => {
    const existing = '00000000-0000-4000-8000-000000315c09';
    await open({
      [CREATE]: () =>
        problemWith(409, 'progress_report_exists', {
          details: { report_id: existing },
        }),
    });
    await click(radio('monthly'));
    await start();
    await settleUntil(() => text().includes('already exists'));
    const region = focused() as HTMLElement;
    expect(region.textContent).toContain(
      'A report for this period already exists.',
    );
    const link = [...main().querySelectorAll('a')].find(
      (anchor) => anchor.textContent === 'Open it',
    );
    expect(link?.getAttribute('href')).toBe(`/progress-reports/${existing}`);
  });

  it.each([
    [
      'report_subject_not_assigned',
      () => problemWith(403, 'report_subject_not_assigned'),
      "You don't teach this student that subject",
    ],
    [
      'subject_unknown',
      () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'subject_id'], code: 'subject_unknown' }],
        }),
      'Choose an existing subject.',
    ],
  ])('%s lands on Subject', async (_code, respond, message) => {
    await open({ [CREATE]: respond });
    const subject = field<HTMLSelectElement>('Subject');
    setFieldValue(subject, PHYSICS.id);
    await start();
    await settleUntil(() => focused() === subject);
    expect(describedText(subject)).toContain(message);
    expect(subject.value).toBe(PHYSICS.id);
  });

  it('progress_report_tutor_only: its message, focused', async () => {
    await open({
      [CREATE]: () => problemWith(403, 'progress_report_tutor_only'),
    });
    await start();
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.textContent?.includes(
        'Only a tutor who currently teaches',
      ),
    );
  });

  it('a 404 on the create shows the not-found page', async () => {
    await open({ [CREATE]: () => problemWith(404, 'not_found') });
    await start();
    await settleUntil(() => text().includes('Page not found'));
  });
});
