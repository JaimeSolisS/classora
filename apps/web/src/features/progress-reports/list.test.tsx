import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { JOSE, JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import { click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  ADMIN_TUTOR,
  APPROVED_PAGE,
  APPROVED_PAGE_NO_CREATE,
  APPROVED_REPORT,
  COORDINATOR,
  EMPTY_APPROVED,
  EMPTY_WORKING,
  FINANCE,
  GUARDIAN,
  OTHER_REPORT_ID,
  QUEUE_PAGE,
  REPORT_ID,
  STUDENT,
  STUDENT_ID,
  TUTOR,
  TUTOR_GUARDIAN,
  TUTOR_PAGE,
} from './fixtures';

// #315 E1, F2, F3, A1, A3, A4, H1 (lists): the working list per audience
// (the queue default for reviewers, every status for tutors, the filters in
// the URL, "Only mine", invalid values ignored, cards, "Load more", every
// empty state, "Clear filters" focusing the heading), the per-student page
// (sections, "New report" by can_create, a former author, the not-found
// page), the approved detail ("Open review page" for reviewers only), the
// route access (no request without access; families sent to their pages),
// the student page link per role and the My schedule link.

const WORKING = 'GET /api/v1/progress-reports';
const APPROVED_LIST = `GET /api/v1/students/${STUDENT_ID}/progress-reports`;
const APPROVED_DETAIL = `GET /api/v1/students/${STUDENT_ID}/progress-reports/${REPORT_ID}`;
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const FAMILY = 'GET /api/v1/schedule/family-students';
const LIST_PAGE = '/progress-reports';
const STUDENT_PAGE = `/students/${STUDENT_ID}/progress-reports`;

let app: RenderedApp | undefined;
let api: ApiStub;
let urls: URL[] = [];
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');
const focused = () => document.activeElement;
const links = () => [...main().querySelectorAll('a')];
const linkTo = (href: string) =>
  links().find((anchor) => anchor.getAttribute('href') === href);

function stub(routes: Routes = {}, as: SessionUserBody = COORDINATOR) {
  urls = [];
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [WORKING]: (request) => {
      urls.push(new URL(request.url));
      return jsonResponse(200, QUEUE_PAGE);
    },
    [APPROVED_LIST]: () => jsonResponse(200, APPROVED_PAGE),
    [APPROVED_DETAIL]: () => jsonResponse(200, APPROVED_REPORT),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [FAMILY]: () => jsonResponse(200, { items: [] }),
    ...routes,
  });
}

async function open(
  path: string,
  routes: Routes = {},
  as: SessionUserBody = COORDINATOR,
) {
  stub(routes, as);
  app = await renderApp(path);
  await settleUntil(() => heading());
}

const workingRoute = (page: unknown) => ({
  [WORKING]: (request: Request) => {
    urls.push(new URL(request.url));
    return jsonResponse(200, page);
  },
});

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('access (A1)', () => {
  it.each([
    ['/progress-reports', WORKING],
    [STUDENT_PAGE, APPROVED_LIST],
    [
      `/progress-reports/${REPORT_ID}`,
      `GET /api/v1/progress-reports/${REPORT_ID}`,
    ],
  ])('Finance on %s: access-denied, no request', async (path, route) => {
    await open(path, {}, FINANCE);
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(route)).toHaveLength(0);
  });

  it.each([
    ['a guardian', GUARDIAN],
    ['a student', STUDENT],
  ] as const)(
    '%s is sent from the staff URLs to the family pages',
    async (_who, as) => {
      await open(LIST_PAGE, {}, as);
      await settleUntil(() => app!.location() === '/my-progress-reports');
      app!.unmount();
      await open(STUDENT_PAGE, {}, as);
      await settleUntil(
        () => app!.location() === `/my-progress-reports?student=${STUDENT_ID}`,
      );
      app!.unmount();
      await open(`${STUDENT_PAGE}/${REPORT_ID}`, {}, as);
      await settleUntil(
        () =>
          app!.location() ===
          `/my-progress-reports/${REPORT_ID}?student=${STUDENT_ID}`,
      );
      expect(api.calls(WORKING)).toHaveLength(0);
      expect(api.calls(APPROVED_LIST)).toHaveLength(0);
    },
  );

  it('a tutor-only user on /my-progress-reports: access-denied', async () => {
    await open('/my-progress-reports', {}, TUTOR);
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(FAMILY)).toHaveLength(0);
  });

  it('a tutor who is also a guardian keeps the staff list', async () => {
    await open(LIST_PAGE, workingRoute(TUTOR_PAGE), TUTOR_GUARDIAN);
    expect(app!.location()).toBe(LIST_PAGE);
    expect(heading()!.textContent).toBe('Progress reports');
  });
});

describe('the working list (E1)', () => {
  it('a reviewer: the queue by default, cards with the author', async () => {
    await open(LIST_PAGE);
    await settleUntil(() => text().includes('Ana García'));
    expect(urls[0]!.searchParams.get('status')).toBe('submitted');
    expect(urls[0]!.searchParams.get('mine')).toBeNull();
    expect(urls[0]!.searchParams.get('limit')).toBe('20');
    const card = linkTo(`/progress-reports/${REPORT_ID}`);
    expect(card?.textContent).toBe('José Ñúñez Pérez');
    expect(text()).toContain('On demand · Oct 1, 2026 – Oct 24, 2026');
    expect(text()).toContain('Weekly · Week of Oct 12, 2026 – Oct 18, 2026');
    expect(text()).toContain('By Carmen Ruiz');
    expect(text()).toContain('In review');
    expect(text()).toContain('Waiting since');
    const status = labelled<HTMLSelectElement>(main(), 'Status');
    expect(status.value).toBe('submitted');
    expect([...status.options].map((option) => option.value)).toEqual([
      'submitted',
      'changes_requested',
      'approved_for_family',
      'retracted',
      'all',
    ]);
    // No "Only mine" for a reviewer who isn't a tutor.
    expect(text()).not.toContain('Only mine');
  });

  it('a tutor: every status by default, drafts offered, no author', async () => {
    await open(LIST_PAGE, workingRoute(TUTOR_PAGE), TUTOR);
    await settleUntil(() => text().includes('Draft'));
    expect(urls[0]!.searchParams.get('status')).toBeNull();
    expect(text()).not.toContain('By Carmen Ruiz');
    const status = labelled<HTMLSelectElement>(main(), 'Status');
    expect(status.value).toBe('all');
    expect([...status.options].map((option) => option.value)).toContain(
      'draft',
    );
    expect(text()).toContain('Approved Oct 26, 2026');
  });

  it('the filters go to the URL and the request', async () => {
    await open(LIST_PAGE);
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Status'),
      'approved_for_family',
    );
    await settle();
    expect(app!.location()).toBe(`${LIST_PAGE}?status=approved_for_family`);
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Report type'),
      'monthly',
    );
    await settleUntil(() =>
      urls.some((url) => url.searchParams.get('kind') === 'monthly'),
    );
    const last = urls.at(-1)!;
    expect(last.searchParams.get('status')).toBe('approved_for_family');
    expect(last.searchParams.get('kind')).toBe('monthly');
    setFieldValue(labelled<HTMLSelectElement>(main(), 'Status'), 'all');
    await settleUntil(() => app!.location().includes('status=all'));
    expect(urls.at(-1)!.searchParams.get('status')).toBeNull();
  });

  it('invalid URL values are ignored', async () => {
    await open(`${LIST_PAGE}?status=nope&kind=yearly&mine=true`);
    expect(urls[0]!.searchParams.get('status')).toBe('submitted');
    expect(urls[0]!.searchParams.get('kind')).toBeNull();
    expect(urls[0]!.searchParams.get('mine')).toBeNull();
  });

  it('a reviewer who is also a tutor: "Only mine"', async () => {
    await open(LIST_PAGE, {}, ADMIN_TUTOR);
    const mine = [...main().querySelectorAll('label')]
      .find((label) => label.textContent === 'Only mine')!
      .querySelector('input')!;
    await click(mine);
    await settleUntil(() =>
      urls.some((url) => url.searchParams.get('mine') === 'true'),
    );
    expect(app!.location()).toContain('mine=true');
  });

  it('Load more follows next_cursor', async () => {
    await open(LIST_PAGE, {
      [WORKING]: (request) => {
        const url = new URL(request.url);
        urls.push(url);
        return jsonResponse(
          200,
          url.searchParams.get('cursor')
            ? { items: [QUEUE_PAGE.items[1]], next_cursor: null }
            : { items: [QUEUE_PAGE.items[0]], next_cursor: 'c-2' },
        );
      },
    });
    await settleUntil(() => text().includes('José'));
    expect(text()).not.toContain('Ana García');
    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => text().includes('Ana García'));
    expect(urls.at(-1)!.searchParams.get('cursor')).toBe('c-2');
    expect(text()).not.toContain('Load more');
  });

  it('the empty queue', async () => {
    await open(LIST_PAGE, workingRoute(EMPTY_WORKING));
    await settleUntil(() =>
      text().includes('No reports are waiting for review.'),
    );
  });

  it('a tutor with no reports: the text and "Open Students"', async () => {
    await open(LIST_PAGE, workingRoute(EMPTY_WORKING), TUTOR);
    await settleUntil(() =>
      text().includes("You haven't written any progress reports yet."),
    );
    expect(linkTo('/students')?.textContent).toBe('Open Students');
  });

  it('filtered empty: "Clear filters" resets the URL and focuses the heading', async () => {
    await open(`${LIST_PAGE}?kind=weekly`, workingRoute(EMPTY_WORKING));
    await settleUntil(() => text().includes('No reports match these filters.'));
    await click(buttonNamed(main(), 'Clear filters'));
    await settleUntil(() => focused() === heading());
    expect(app!.location()).toBe(LIST_PAGE);
  });
});

describe('the per-student page (F2)', () => {
  it('the name, Back, New report, both sections', async () => {
    await open(STUDENT_PAGE, workingRoute(TUTOR_PAGE), TUTOR);
    await settleUntil(() => text().includes('José'));
    expect(heading()!.textContent).toBe('Progress reports: José Ñúñez Pérez');
    expect(linkTo(`/students/${STUDENT_ID}`)?.textContent).toBe(
      'Back to student',
    );
    expect(linkTo(`${STUDENT_PAGE}/new`)?.textContent).toBe('New report');
    expect(urls[0]!.searchParams.get('student_id')).toBe(STUDENT_ID);
    expect(text()).toContain('In progress and in review');
    // Working cards open the working page; approved ones the approved page.
    expect(linkTo(`/progress-reports/${REPORT_ID}`)).toBeTruthy();
    expect(linkTo(`${STUDENT_PAGE}/${REPORT_ID}`)?.textContent).toBe(
      'Monthly · September 2026',
    );
    expect(text()).toContain('Written by Carmen Ruiz');
  });

  it('no can_create: no New report; empty working section hidden', async () => {
    await open(
      STUDENT_PAGE,
      {
        ...workingRoute(EMPTY_WORKING),
        [APPROVED_LIST]: () => jsonResponse(200, APPROVED_PAGE_NO_CREATE),
      },
      COORDINATOR,
    );
    await settleUntil(() => text().includes('Approved'));
    expect(linkTo(`${STUDENT_PAGE}/new`)).toBeUndefined();
    expect(text()).not.toContain('In progress and in review');
  });

  it('no approved reports yet', async () => {
    await open(
      STUDENT_PAGE,
      { [APPROVED_LIST]: () => jsonResponse(200, EMPTY_APPROVED) },
      TUTOR,
    );
    await settleUntil(() => text().includes('No approved reports yet.'));
  });

  it('a former author: no name (the student is 404), the list still shows', async () => {
    await open(
      STUDENT_PAGE,
      {
        [STUDENT_ROUTE]: () => problemWith(404, 'not_found'),
        [APPROVED_LIST]: () => jsonResponse(200, APPROVED_PAGE_NO_CREATE),
      },
      TUTOR,
    );
    await settleUntil(() => text().includes('Monthly · September 2026'));
    expect(heading()!.textContent).toBe('Progress reports');
    expect(text()).not.toContain('Back to student');
  });

  it('a 404 on the approved list: the not-found page', async () => {
    await open(
      STUDENT_PAGE,
      { [APPROVED_LIST]: () => problemWith(404, 'not_found') },
      TUTOR,
    );
    await settleUntil(() => text().includes('Page not found'));
  });
});

describe('the approved detail (F3)', () => {
  it('a reviewer gets "Open review page"', async () => {
    await open(`${STUDENT_PAGE}/${REPORT_ID}`);
    await settleUntil(() => text().includes('Buen avance'));
    expect(text()).toContain('Monthly · September 2026');
    expect(text()).toContain('Written by Carmen Ruiz');
    expect(text()).toContain('Approved on Oct 2, 2026');
    expect(text()).toContain('Facts as approved');
    expect(linkTo(`/progress-reports/${REPORT_ID}`)?.textContent).toBe(
      'Open review page',
    );
    // Never a status or a reason.
    expect(text()).not.toContain('In review');
  });

  it('a tutor does not', async () => {
    await open(`${STUDENT_PAGE}/${REPORT_ID}`, {}, TUTOR);
    await settleUntil(() => text().includes('Buen avance'));
    expect(linkTo(`/progress-reports/${REPORT_ID}`)).toBeUndefined();
  });

  it('a 404: the not-found page', async () => {
    await open(
      `${STUDENT_PAGE}/${OTHER_REPORT_ID}`,
      {
        [`GET /api/v1/students/${STUDENT_ID}/progress-reports/${OTHER_REPORT_ID}`]:
          () => problemWith(404, 'not_found'),
      },
      TUTOR,
    );
    await settleUntil(() => text().includes('Page not found'));
  });
});

describe('the student page link (A3)', () => {
  it.each([
    ['a reviewer', COORDINATOR, true],
    ['a tutor', TUTOR, true],
    ['Finance', FINANCE, false],
    ['a guardian', GUARDIAN, false],
    ['a student', STUDENT, false],
  ] as const)('%s: %s', async (_who, as, shown) => {
    await open(
      `/students/${STUDENT_ID}`,
      { [STUDENT_ROUTE]: () => jsonResponse(200, JOSE) },
      as,
    );
    await settleUntil(() => heading()?.textContent?.includes('José'));
    const link = linkTo(STUDENT_PAGE);
    expect(Boolean(link)).toBe(shown);
    if (link) {
      expect(link.textContent).toBe('Progress reports');
    }
  });
});

describe('My schedule: "Progress reports of {name}" (A4)', () => {
  const NOW = Date.parse('2026-11-09T16:30:00Z');
  const NAME = 'José Ñúñez Pérez';
  const OTHER_ID = '00000000-0000-4000-8000-000000315e02';

  async function dialogFor(
    state: string,
    audience: 'tutor' | 'family' = 'tutor',
  ) {
    stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const item = {
      ...tutorItem(),
      state,
      starts_at: '2026-11-09T16:00:00Z',
      participants: [
        { student_id: STUDENT_ID, display_name: NAME },
        { student_id: OTHER_ID, display_name: 'Ana García' },
      ],
    } as ReturnType<typeof tutorItem>;
    const entry =
      audience === 'tutor'
        ? ({ audience: 'tutor', item } as const)
        : ({
            audience: 'family',
            item: {
              id: item.id,
              kind: item.kind,
              state: item.state,
              starts_at: item.starts_at,
              ends_at: item.ends_at,
              subject: item.subject,
              tutor: { display_name: 'Carmen Ruiz' },
              classroom: null,
              attendance: null,
            },
          } as never);
    app = await renderApp('/', [
      {
        path: '/',
        element: createElement(EntryDialog, {
          entry,
          context: {
            t: i18n.getFixedT('en', 'common'),
            timeZone: 'America/Mexico_City',
            language: 'en',
          },
          onClose: () => undefined,
          onReturnFocus: () => undefined,
          now: () => NOW,
        }),
      },
    ]);
    await settle();
    return [
      ...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a'),
    ];
  }

  const named = (anchors: HTMLAnchorElement[], name: string) =>
    anchors.find((a) => a.getAttribute('aria-label') === name);

  it.each([
    ['confirmed', true],
    ['completed', true],
    ['offered', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ])('a tutor %s entry: %s', async (state, shown) => {
    const anchors = await dialogFor(state);
    const reports = named(anchors, `Progress reports of ${NAME}`);
    expect(Boolean(reports)).toBe(shown);
    expect(Boolean(named(anchors, 'Progress reports of Ana García'))).toBe(
      shown,
    );
    // The earlier links stay beside it.
    expect(Boolean(named(anchors, `Learning plans of ${NAME}`))).toBe(shown);
    expect(Boolean(named(anchors, `Academic history of ${NAME}`))).toBe(shown);
    if (reports) {
      expect(reports.textContent).toBe('Progress reports');
      expect(reports.getAttribute('href')).toBe(STUDENT_PAGE);
    }
  });

  it('nothing for the family audience', async () => {
    const anchors = await dialogFor('confirmed', 'family');
    expect(named(anchors, `Progress reports of ${NAME}`)).toBeUndefined();
  });
});
