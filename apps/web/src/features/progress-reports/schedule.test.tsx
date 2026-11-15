import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import {
  click,
  labelled,
  openDialog,
  pressEscape,
  setFieldValue,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  APPROVED_PAGE,
  COORDINATOR,
  EMPTY_WORKING,
  STUDENT_ID,
  TUTOR,
} from './fixtures';
import {
  AUTHORS,
  CARMEN,
  DIEGO,
  EVERY_REASON,
  EVERY_STATUS,
  INACTIVE,
  NO_SCHEDULES,
  NO_TUTOR,
  SCHEDULES,
  SUBMITTED_REPORT_ID,
  WEEKLY_DRAFT,
  scheduleOf,
} from './scheduleFixtures';

// #316 E1-E7, F7: the "Report schedule" panel on the per-student progress
// reports page: shown to progress_report.review holders only; every
// latest-period status and ineligible reason; the inactive section; no
// eligible tutor; the add dialog (tutor → subjects reset, first periods in
// the center's calendar, the body, every refusal on its field or the form);
// deactivate / reactivate with the version (the conflict's Reload); focus
// after every step.

const PAGE = `/students/${STUDENT_ID}/progress-reports`;
const S1 = `GET /api/v1/students/${STUDENT_ID}/progress-report-schedules`;
const S2 = `POST /api/v1/students/${STUDENT_ID}/progress-report-schedules`;
const s3 = (id: string) => `PATCH /api/v1/progress-report-schedules/${id}`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const panel = () =>
  [...main().querySelectorAll('section')].find(
    (section) => section.querySelector('h2')?.textContent === 'Report schedule',
  );
const panelText = () => panel()?.textContent ?? '';
const dialog = () => openDialog()!;
const focused = () => document.activeElement;
const rows = () => [...(panel()?.querySelectorAll('li') ?? [])];
const rowOf = (text: string) =>
  rows().find((row) => row.textContent?.includes(text))!;

function stub(routes: Routes = {}, as: SessionUserBody = COORDINATOR) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    'GET /api/v1/progress-reports': () => jsonResponse(200, EMPTY_WORKING),
    [`GET /api/v1/students/${STUDENT_ID}/progress-reports`]: () =>
      jsonResponse(200, APPROVED_PAGE),
    [`GET /api/v1/students/${STUDENT_ID}`]: () =>
      jsonResponse(200, JOSE_LIMITED),
    [S1]: () => jsonResponse(200, SCHEDULES),
    ...routes,
  });
}

async function open(routes: Routes = {}, as: SessionUserBody = COORDINATOR) {
  stub(routes, as);
  app = await renderApp(PAGE);
  await settleUntil(() => main().querySelector('h1'));
  if (as === COORDINATOR) {
    await settleUntil(
      () => panel()?.querySelector('[data-slot="loading-state"]') === null,
    );
    await settleUntil(() =>
      main().querySelector('h1')?.textContent?.includes('José'),
    );
  }
}

async function openAdd() {
  await click(buttonNamed(panel()!, 'Add schedule'));
  await settleUntil(() => openDialog());
}

const select = (label: string) => labelled<HTMLSelectElement>(dialog(), label);
const options = (label: string) =>
  [...select(label).options].map((option) => option.textContent);
const radio = (label: string) =>
  [...dialog().querySelectorAll<HTMLInputElement>('input[type="radio"]')].find(
    (input) => input.parentElement?.textContent === label,
  )!;

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  // Wednesday 21 Oct 2026, 12:00 in the center (America/Mexico_City).
  vi.setSystemTime(new Date('2026-10-21T18:00:00Z'));
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('who sees the panel (E1)', () => {
  it('a reviewer: the heading and the sentence', async () => {
    await open();
    expect(panelText()).toContain(
      'The tutor gets an empty draft after each week or month ends. A reviewer still approves it before families see it.',
    );
    expect(api.calls(S1)).toHaveLength(1);
  });

  it('a tutor: nothing new, no request', async () => {
    await open({}, TUTOR);
    await settle();
    expect(panel()).toBeUndefined();
    expect(api.calls(S1)).toHaveLength(0);
  });
});

describe('the list (E2)', () => {
  it('the rows, the latest period, the warning, the inactive section', async () => {
    await open();
    const weekly = rowOf('Carmen Ruiz');
    expect(weekly.textContent).toContain('Weekly · All subjects');
    expect(weekly.textContent).toContain('Since Oct 5, 2026');
    expect(weekly.textContent).toContain(
      'Week of Oct 12, 2026 – Oct 18, 2026: draft started',
    );
    // A draft is the tutor's: no link.
    expect(weekly.querySelector('a')).toBeNull();
    const monthly = rows().find((row) =>
      row.textContent?.includes('Monthly · Matemáticas'),
    )!;
    expect(monthly.textContent).toContain('September 2026: submitted');
    expect(monthly.querySelector('a')!.getAttribute('href')).toBe(
      `/progress-reports/${SUBMITTED_REPORT_ID}`,
    );
    const warned = rows().find((row) =>
      row.textContent?.includes('Weekly · Física'),
    )!;
    expect(warned.textContent).toContain(
      'Carmen Ruiz no longer teaches José Ñúñez Pérez, so no draft is created.',
    );
    expect(warned.textContent).toContain('not created yet');
    const inactive = panel()!.querySelector('h3')!;
    expect(inactive.textContent).toBe('Inactive schedules');
    const section = inactive.closest('section')!;
    expect(section.textContent).toContain('Diego Mora');
    expect(buttonNamed(section, 'Reactivate')).toBeTruthy();
    // Each action names its row.
    expect(
      buttonNamed(panel()!, 'Deactivate: Carmen Ruiz, Weekly · All subjects'),
    ).toBeTruthy();
    expect(
      buttonNamed(section, 'Reactivate: Diego Mora, Monthly · All subjects'),
    ).toBeTruthy();
  });

  it('every latest-period status', async () => {
    await open({ [S1]: () => jsonResponse(200, EVERY_STATUS) });
    const expected = [
      ['not created yet', false],
      ['draft started', false],
      ['deleted by the tutor', false],
      ['submitted', true],
      ['changes requested', true],
      ['approved', true],
      ['retracted', true],
    ] as const;
    expected.forEach(([status, linked], index) => {
      const row = rowOf(`Tutor ${index + 1}`);
      expect(row.textContent).toContain(
        `Week of Oct 12, 2026 – Oct 18, 2026: ${status}`,
      );
      expect(Boolean(row.querySelector('a')), status).toBe(linked);
    });
  });

  it('every ineligible reason, as text', async () => {
    await open({ [S1]: () => jsonResponse(200, EVERY_REASON) });
    const text = panelText();
    for (const warning of [
      'The student is inactive.',
      'Carmen Ruiz no longer has the tutor role.',
      'Carmen Ruiz no longer teaches José Ñúñez Pérez, so no draft is created.',
      'The subject is inactive.',
      'Carmen Ruiz no longer teaches this subject to José Ñúñez Pérez.',
    ]) {
      expect(text).toContain(warning);
    }
  });

  it('empty', async () => {
    await open({ [S1]: () => jsonResponse(200, NO_SCHEDULES) });
    expect(panelText()).toContain('No report schedule yet.');
    expect(rows()).toHaveLength(0);
  });

  it('nobody teaches the student: Add schedule says why and opens nothing', async () => {
    await open({ [S1]: () => jsonResponse(200, NO_TUTOR) });
    const add = buttonNamed(panel()!, 'Add schedule');
    expect(add.getAttribute('aria-disabled')).toBe('true');
    expect(add.hasAttribute('disabled')).toBe(false);
    const reason = document.getElementById(
      add.getAttribute('aria-describedby')!,
    )!;
    expect(reason.textContent).toBe(
      'No tutor currently teaches José Ñúñez Pérez.',
    );
    await click(add);
    expect(openDialog()).toBeNull();
  });

  it('a refused list shows the error with Try again', async () => {
    await open({ [S1]: () => problemWith(403, 'permission_denied') });
    await settleUntil(() => panelText().includes('Try again'));
  });
});

describe('add schedule (E3, E5)', () => {
  it('tutor → subjects (reset on change), cadence → first periods, the body', async () => {
    await open({
      [S2]: () => jsonResponse(201, scheduleOf()),
    });
    await openAdd();
    expect(options('Tutor')).toEqual([
      'Choose a tutor',
      'Carmen Ruiz',
      'Diego Mora',
    ]);
    expect(options('Subject')).toEqual(['All subjects']);
    setFieldValue(select('Tutor'), CARMEN.user_id);
    await settle();
    expect(options('Subject')).toEqual([
      'All subjects',
      'Física',
      'Matemáticas',
    ]);
    setFieldValue(select('Subject'), AUTHORS[0]!.subjects[0]!.id);
    setFieldValue(select('Tutor'), DIEGO.user_id);
    await settle();
    expect(select('Subject').value).toBe('');
    expect(options('Subject')).toEqual(['All subjects', 'Matemáticas']);
    setFieldValue(select('Subject'), AUTHORS[1]!.subjects[0]!.id);
    expect(radio('Weekly').checked).toBe(true);
    expect(select('First period').value).toBe('2026-10-19');
    await click(radio('Monthly'));
    expect(select('First period').value).toBe('2026-10-01');
    setFieldValue(select('First period'), '2026-12-01');
    await click(buttonNamed(dialog(), 'Add schedule'));
    await settleUntil(() => openDialog() === null);
    expect(api.calls(S2)[0]!.body).toEqual({
      author_user_id: DIEGO.user_id,
      cadence: 'monthly',
      subject_id: AUTHORS[1]!.subjects[0]!.id,
      starts_on: '2026-12-01',
    });
    // S1 refetched, focus back on "Add schedule", the status message.
    expect(api.calls(S1)).toHaveLength(2);
    await settleUntil(() => focused()?.textContent === 'Add schedule');
    expect(panelText()).toContain('Schedule added.');
  });

  it('"All subjects" sends no subject_id', async () => {
    await open({ [S2]: () => jsonResponse(201, scheduleOf()) });
    await openAdd();
    setFieldValue(select('Tutor'), CARMEN.user_id);
    await click(buttonNamed(dialog(), 'Add schedule'));
    await settleUntil(() => api.calls(S2).length === 1);
    expect(api.calls(S2)[0]!.body).toEqual({
      author_user_id: CARMEN.user_id,
      cadence: 'weekly',
      starts_on: '2026-10-19',
    });
  });

  it('the first periods on a Sunday night in Mexico City', async () => {
    // Sunday 25 Oct, 23:30 in the center (05:30Z on Monday).
    vi.setSystemTime(new Date('2026-10-26T05:30:00Z'));
    await open();
    await openAdd();
    const weeks = options('First period');
    expect(weeks).toHaveLength(12);
    expect(weeks[0]).toBe('Week of Oct 19, 2026');
    expect(weeks[11]).toBe('Week of Jan 4, 2027');
    await click(radio('Monthly'));
    const months = options('First period');
    expect(months).toHaveLength(12);
    expect(months[0]).toBe('October 2026');
    expect(months[11]).toBe('September 2027');
  });

  describe('with the host in Asia/Tokyo', () => {
    let previousZone: string | undefined;
    beforeEach(() => {
      previousZone = process.env.TZ;
      process.env.TZ = 'Asia/Tokyo';
    });
    afterEach(() => {
      process.env.TZ = previousZone;
    });

    it("still the center's week (Tokyo is already on Monday)", async () => {
      vi.setSystemTime(new Date('2026-10-26T05:30:00Z'));
      await open();
      await openAdd();
      expect(options('First period')[0]).toBe('Week of Oct 19, 2026');
      expect(select('First period').value).toBe('2026-10-19');
    });
  });

  it('Escape and Cancel return focus to Add schedule; nothing is sent', async () => {
    await open();
    await openAdd();
    await pressEscape();
    expect(openDialog()).toBeNull();
    await settleUntil(() => focused()?.textContent === 'Add schedule');
    await openAdd();
    await click(buttonNamed(dialog(), 'Cancel'));
    expect(openDialog()).toBeNull();
    await settleUntil(() => focused()?.textContent === 'Add schedule');
    expect(api.calls(S2)).toHaveLength(0);
  });

  it('no tutor chosen: the message on the field, nothing sent', async () => {
    await open();
    await openAdd();
    await click(buttonNamed(dialog(), 'Add schedule'));
    await settleUntil(() => dialog().textContent?.includes('Choose a tutor.'));
    expect(focused()).toBe(select('Tutor'));
    expect(select('Tutor').getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(S2)).toHaveLength(0);
  });

  it.each([
    [
      'author_user_id',
      'schedule_author_invalid',
      'Tutor',
      "This tutor doesn't currently teach the student. Choose another tutor.",
    ],
    [
      'subject_id',
      'schedule_subject_not_assigned',
      'Subject',
      "The tutor doesn't currently teach the student this subject.",
    ],
    ['subject_id', 'subject_unknown', 'Subject', 'Choose an existing subject.'],
    [
      'starts_on',
      'schedule_starts_on_invalid',
      'First period',
      'Choose the current period or a later one, at most a year ahead.',
    ],
  ])('%s %s lands on its field', async (field, code, label, message) => {
    await open({
      [S2]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', field], code }],
        }),
    });
    await openAdd();
    setFieldValue(select('Tutor'), CARMEN.user_id);
    await click(buttonNamed(dialog(), 'Add schedule'));
    await settleUntil(() => dialog().textContent?.includes(message));
    await settleUntil(() => focused() === select(label));
    expect(select(label).getAttribute('aria-invalid')).toBe('true');
    expect(openDialog()).not.toBeNull();
  });

  it('an active schedule like it: the message, focused', async () => {
    await open({
      [S2]: () =>
        problemWith(409, 'progress_report_schedule_exists', {
          details: { schedule_id: WEEKLY_DRAFT.id },
        }),
    });
    await openAdd();
    setFieldValue(select('Tutor'), CARMEN.user_id);
    await click(buttonNamed(dialog(), 'Add schedule'));
    const message =
      'This tutor already has an active weekly schedule for this subject.';
    await settleUntil(() => dialog().textContent?.includes(message));
    await settleUntil(() => focused()?.textContent?.includes(message));
  });

  it.each([
    [422, 'student_inactive', 'This student is inactive.'],
    [403, 'permission_denied', "You don't have permission"],
  ])('a %i %s shows in the dialog', async (status, code, message) => {
    await open({ [S2]: () => problemWith(status, code) });
    await openAdd();
    setFieldValue(select('Tutor'), CARMEN.user_id);
    await click(buttonNamed(dialog(), 'Add schedule'));
    await settleUntil(() => dialog().textContent?.includes(message));
    await settleUntil(() => focused()?.textContent?.includes(message));
  });
});

describe('deactivate and reactivate (E4, E5)', () => {
  it('deactivate: the text, the version, refetch, focus on the status', async () => {
    await open({
      [s3(WEEKLY_DRAFT.id)]: () => jsonResponse(200, WEEKLY_DRAFT),
    });
    await click(
      buttonNamed(panel()!, 'Deactivate: Carmen Ruiz, Weekly · All subjects'),
    );
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain('Deactivate this schedule?');
    expect(dialog().textContent).toContain(
      'No new drafts are created. Reports already written stay as they are.',
    );
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => openDialog() === null);
    expect(api.calls(s3(WEEKLY_DRAFT.id))[0]!.body).toEqual({
      version: 1,
      is_active: false,
    });
    expect(api.calls(S1)).toHaveLength(2);
    await settleUntil(() => focused()?.textContent === 'Schedule deactivated.');
    expect(focused()?.getAttribute('role')).toBe('status');
  });

  it('reactivate: the text and the body', async () => {
    await open({ [s3(INACTIVE.id)]: () => jsonResponse(200, INACTIVE) });
    await click(
      buttonNamed(panel()!, 'Reactivate: Diego Mora, Monthly · All subjects'),
    );
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain(
      'The next draft is for the first period that ends from now on.',
    );
    await click(buttonNamed(dialog(), 'Reactivate'));
    await settleUntil(() => openDialog() === null);
    expect(api.calls(s3(INACTIVE.id))[0]!.body).toEqual({
      version: 1,
      is_active: true,
    });
    await settleUntil(() => focused()?.textContent === 'Schedule reactivated.');
  });

  it('Escape and Cancel return focus to the row button', async () => {
    await open();
    const name = 'Deactivate: Carmen Ruiz, Weekly · All subjects';
    await click(buttonNamed(panel()!, name));
    await settleUntil(() => openDialog());
    await pressEscape();
    await settleUntil(() => focused()?.getAttribute('aria-label') === name);
    await click(buttonNamed(panel()!, name));
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => focused()?.getAttribute('aria-label') === name);
    expect(api.calls(s3(WEEKLY_DRAFT.id))).toHaveLength(0);
  });

  it('a conflict: "changed elsewhere" + Reload refetches and focuses the heading', async () => {
    await open({
      [s3(WEEKLY_DRAFT.id)]: () => problemWith(409, 'version_conflict'),
    });
    await click(
      buttonNamed(panel()!, 'Deactivate: Carmen Ruiz, Weekly · All subjects'),
    );
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() =>
      dialog().textContent?.includes('This schedule changed elsewhere.'),
    );
    await settleUntil(() =>
      focused()?.textContent?.includes('This schedule changed elsewhere.'),
    );
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => openDialog() === null);
    expect(api.calls(S1)).toHaveLength(2);
    await settleUntil(() => focused()?.textContent === 'Report schedule');
  });

  it('a refused reactivation shows its message in the dialog', async () => {
    await open({
      [s3(INACTIVE.id)]: () => problemWith(422, 'schedule_author_invalid'),
    });
    await click(
      buttonNamed(panel()!, 'Reactivate: Diego Mora, Monthly · All subjects'),
    );
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Reactivate'));
    await settleUntil(() =>
      dialog().textContent?.includes("This tutor doesn't currently teach"),
    );
    expect(dialog().textContent).not.toContain('Reload');
  });
});
