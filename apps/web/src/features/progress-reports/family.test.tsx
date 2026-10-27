import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
  SELF_ONLY,
  SIBLING_ID,
} from '@/features/academics/history/fixtures';
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
  APPROVED_ITEM,
  APPROVED_PAGE,
  APPROVED_REPORT,
  GUARDIAN,
  REPORT_ID,
  SCRIPT,
  STUDENT,
} from './fixtures';

// #315 F4-F6, H1 (family): the picker (the default, ?student=, a foreign
// value falling back with no request for it, one entry, none), an adult
// who hasn't shared (no request), the list (cards, "Load more", empty), a
// 403 consent and both inline 404 messages, the detail (narrative and
// facts, never a status or a reason; "Back to reports" with the student),
// Try again focusing the heading, and plain text. #341: without a usable
// ?student=, the report's student among the readable ones.

const FAMILY = 'GET /api/v1/schedule/family-students';
const FOREIGN_ID = '00000000-0000-4000-8000-000000315f99';
const listOf = (id: string) => `GET /api/v1/students/${id}/progress-reports`;
const detailOf = (id: string) =>
  `GET /api/v1/students/${id}/progress-reports/${REPORT_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');
const focused = () => document.activeElement;
const linkTo = (href: string) =>
  [...main().querySelectorAll('a')].find(
    (anchor) => anchor.getAttribute('href') === href,
  );

async function open(path: string, routes: Routes = {}, as = GUARDIAN) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [FAMILY]: () => jsonResponse(200, FAMILY_STUDENTS),
    [listOf(CHILD_ID)]: () => jsonResponse(200, APPROVED_PAGE),
    [listOf(SIBLING_ID)]: () =>
      jsonResponse(200, { ...APPROVED_PAGE, items: [] }),
    [detailOf(CHILD_ID)]: () => jsonResponse(200, APPROVED_REPORT),
    ...routes,
  });
  app = await renderApp(path);
  await settleUntil(() => heading());
}

const reports = () =>
  vi
    .mocked(console.error)
    .mock.calls.filter((call) => call[0] === '[classora]');

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('the family list (F4)', () => {
  it('the picker defaults to the first student; cards link with ?student=', async () => {
    await open('/my-progress-reports');
    expect(heading()!.textContent).toBe('Tutor reports');
    await settleUntil(() => text().includes('Monthly · September 2026'));
    const whose = labelled<HTMLSelectElement>(main(), 'Whose reports');
    expect(whose.value).toBe(CHILD_ID);
    expect(
      linkTo(`/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`)
        ?.textContent,
    ).toBe('Monthly · September 2026');
    expect(text()).toContain('Física');
    expect(text()).toContain('Written by Carmen Ruiz');
    expect(text()).toContain('Approved Oct 2, 2026');
    // Never a status.
    expect(text()).not.toContain('In review');
  });

  it('?student= picks; changing the picker updates the URL', async () => {
    await open(`/my-progress-reports?student=${SIBLING_ID}`);
    await settleUntil(() =>
      text().includes('No reports have been shared yet.'),
    );
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Whose reports'),
      CHILD_ID,
    );
    await settleUntil(
      () => app!.location() === `/my-progress-reports?student=${CHILD_ID}`,
    );
    await settleUntil(() => text().includes('Monthly · September 2026'));
  });

  it('a foreign ?student= falls back to the first, with no request for it', async () => {
    await open(`/my-progress-reports?student=${FOREIGN_ID}`);
    await settleUntil(() => text().includes('Monthly · September 2026'));
    expect(api.calls(listOf(FOREIGN_ID))).toHaveLength(0);
  });

  it("an adult who hasn't shared: the consent message, no request", async () => {
    await open(`/my-progress-reports?student=${ADULT_ID}`);
    await settleUntil(() =>
      text().includes("hasn't shared their academic records"),
    );
    expect(api.calls(listOf(ADULT_ID))).toHaveLength(0);
  });

  it('one entry: plain text "My reports"; none: the message', async () => {
    await open(
      '/my-progress-reports',
      {
        [FAMILY]: () => jsonResponse(200, SELF_ONLY),
        [listOf(SELF_ONLY.items[0]!.student_id)]: () =>
          jsonResponse(200, APPROVED_PAGE),
      },
      STUDENT,
    );
    await settleUntil(() => text().includes('My reports'));
    expect(main().querySelector('select')).toBeNull();
    app!.unmount();
    await open('/my-progress-reports', {
      [FAMILY]: () => jsonResponse(200, { items: [] }),
    });
    await settleUntil(() =>
      text().includes("There's no student linked to your account."),
    );
  });

  it('a 403 consent and a 404 are shown inline; the picker stays', async () => {
    await open('/my-progress-reports', {
      [listOf(CHILD_ID)]: () => problemWith(403, 'student_consent_required'),
    });
    await settleUntil(() =>
      text().includes("hasn't shared their academic records"),
    );
    app!.unmount();
    await open('/my-progress-reports', {
      [listOf(CHILD_ID)]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() => text().includes("These reports aren't available."));
    expect(labelled<HTMLSelectElement>(main(), 'Whose reports')).toBeTruthy();
    expect(text()).not.toContain('Page not found');
    // QA D1: the inline 404 is a designed state, never reported.
    expect(
      reports().filter((call) => JSON.stringify(call).includes('"status":404')),
    ).toHaveLength(0);
  });

  it('Load more follows next_cursor', async () => {
    const second = {
      ...APPROVED_ITEM,
      id: '00000000-0000-4000-8000-000000315a09',
      kind: 'weekly' as const,
      period_start: '2026-08-03',
      period_end: '2026-08-09',
    };
    await open('/my-progress-reports', {
      [listOf(CHILD_ID)]: (request) =>
        jsonResponse(
          200,
          new URL(request.url).searchParams.get('cursor')
            ? { ...APPROVED_PAGE, items: [second], next_cursor: null }
            : { ...APPROVED_PAGE, next_cursor: 'c-2' },
        ),
    });
    await settleUntil(() => text().includes('Load more'));
    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => text().includes('Week of Aug 3, 2026'));
  });
});

describe('the family detail (F5, F6)', () => {
  it('the report with its facts; Back keeps the student', async () => {
    await open(`/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`);
    await settleUntil(() => text().includes('Buen avance en física.'));
    expect(heading()!.textContent).toBe('Progress report');
    expect(text()).toContain('Written by Carmen Ruiz');
    expect(text()).toContain('Practicar problemas.');
    expect(text()).toContain('Facts as approved');
    expect(text()).not.toContain('Areas for improvement');
    expect(text()).not.toContain('In review');
    expect(
      linkTo(`/my-progress-reports?student=${CHILD_ID}`)?.textContent,
    ).toBe('Back to reports');
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  // #341: a staff link (no ?student=) or a foreign ?student= asks the
  // detail route for each readable family student, never anyone else.
  it("without ?student=, the report's student is found; the URL names them", async () => {
    await open(`/my-progress-reports/${REPORT_ID}`, {
      [detailOf(CHILD_ID)]: () => problemWith(404, 'not_found'),
      [detailOf(SIBLING_ID)]: () => jsonResponse(200, APPROVED_REPORT),
    });
    await settleUntil(() => text().includes('Buen avance en física.'));
    await settleUntil(
      () =>
        app!.location() ===
        `/my-progress-reports/${REPORT_ID}?student=${SIBLING_ID}`,
    );
    expect(
      linkTo(`/my-progress-reports?student=${SIBLING_ID}`)?.textContent,
    ).toBe('Back to reports');
    // Requested once: the probe's data is reused after the URL change.
    expect(api.calls(detailOf(SIBLING_ID))).toHaveLength(1);
    expect(api.calls(detailOf(CHILD_ID))).toHaveLength(1);
    // Never an adult who hasn't shared.
    expect(api.calls(detailOf(ADULT_ID))).toHaveLength(0);
    // The other child's 404 is expected, never reported.
    expect(reports()).toHaveLength(0);
  });

  it('without ?student=, a report of the first student', async () => {
    await open(`/my-progress-reports/${REPORT_ID}`);
    await settleUntil(() => text().includes('Buen avance en física.'));
    await settleUntil(
      () =>
        app!.location() ===
        `/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`,
    );
    expect(api.calls(detailOf(CHILD_ID))).toHaveLength(1);
  });

  it('a foreign ?student=: the readable students are asked, never it', async () => {
    await open(`/my-progress-reports/${REPORT_ID}?student=${FOREIGN_ID}`, {
      [detailOf(CHILD_ID)]: () => problemWith(404, 'not_found'),
      [detailOf(SIBLING_ID)]: () => jsonResponse(200, APPROVED_REPORT),
    });
    await settleUntil(() => text().includes('Buen avance en física.'));
    await settleUntil(
      () =>
        app!.location() ===
        `/my-progress-reports/${REPORT_ID}?student=${SIBLING_ID}`,
    );
    expect(api.calls(detailOf(FOREIGN_ID))).toHaveLength(0);
    expect(api.calls(detailOf(ADULT_ID))).toHaveLength(0);
  });

  it('without ?student=, every student 404: no longer available, not reported', async () => {
    await open(`/my-progress-reports/${REPORT_ID}`, {
      [detailOf(CHILD_ID)]: () => problemWith(404, 'not_found'),
      [detailOf(SIBLING_ID)]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() =>
      text().includes('This report is no longer available.'),
    );
    // No student is known: both links go to the list.
    expect(
      [...main().querySelectorAll('a')]
        .filter((a) => a.getAttribute('href') === '/my-progress-reports')
        .map((a) => a.textContent),
    ).toEqual(['Back to reports', 'Back to tutor reports']);
    expect(app!.location()).toBe(`/my-progress-reports/${REPORT_ID}`);
    expect(api.calls(detailOf(ADULT_ID))).toHaveLength(0);
    expect(reports()).toHaveLength(0);
  });

  it('without ?student=, a failure and a 404: Try again finds the report', async () => {
    await open(`/my-progress-reports/${REPORT_ID}`, {
      [detailOf(CHILD_ID)]: [
        () => problemWith(500, 'internal_error'),
        () => jsonResponse(200, APPROVED_REPORT),
      ],
      [detailOf(SIBLING_ID)]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() => text().includes('Try again'));
    expect(text()).not.toContain('This report is no longer available.');
    // Only the unexpected failure is reported.
    expect(reports()).toHaveLength(1);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => text().includes('Buen avance en física.'));
    await settleUntil(
      () =>
        app!.location() ===
        `/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`,
    );
    await settleUntil(() => focused() === heading());
    // Try again asks again only for the failed student.
    expect(api.calls(detailOf(SIBLING_ID))).toHaveLength(1);
  });

  it('one entry without ?student=: one request, the report', async () => {
    const selfId = SELF_ONLY.items[0]!.student_id;
    await open(
      `/my-progress-reports/${REPORT_ID}`,
      {
        [FAMILY]: () => jsonResponse(200, SELF_ONLY),
        [detailOf(selfId)]: () => jsonResponse(200, APPROVED_REPORT),
      },
      STUDENT,
    );
    await settleUntil(() => text().includes('Buen avance en física.'));
    expect(api.calls(detailOf(selfId))).toHaveLength(1);
    expect(api.calls(detailOf(CHILD_ID))).toHaveLength(0);
  });

  it('a 404: "This report is no longer available." inline, with the list link', async () => {
    await open(`/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`, {
      [detailOf(CHILD_ID)]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() =>
      text().includes('This report is no longer available.'),
    );
    expect(text()).not.toContain('Page not found');
    expect(linkTo('/my-progress-reports')?.textContent).toBe(
      'Back to tutor reports',
    );
    // QA D1: the expected 404 isn't logged to the console.
    expect(reports()).toHaveLength(0);
  });

  it('a 403 consent: the consent message', async () => {
    await open(`/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`, {
      [detailOf(CHILD_ID)]: () => problemWith(403, 'student_consent_required'),
    });
    await settleUntil(() =>
      text().includes("hasn't shared their academic records"),
    );
  });

  it('an error: Try again refetches, then focuses the heading', async () => {
    await open(`/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`, {
      [detailOf(CHILD_ID)]: [
        () => problemWith(500, 'internal_error'),
        () => jsonResponse(200, APPROVED_REPORT),
      ],
    });
    await settleUntil(() => text().includes('Try again'));
    // An unexpected failure is still reported.
    expect(reports()).toHaveLength(1);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => text().includes('Buen avance en física.'));
    await settleUntil(() => focused() === heading());
  });

  it('plain text: markup shows literally', async () => {
    await open(`/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`, {
      [detailOf(CHILD_ID)]: () =>
        jsonResponse(200, { ...APPROVED_REPORT, summary: SCRIPT }),
    });
    await settleUntil(() => text().includes(SCRIPT));
    expect(main().querySelector('script')).toBeNull();
  });
});
