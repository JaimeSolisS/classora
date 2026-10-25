import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
  SELF_ONLY,
  SIBLING_ID,
} from '@/features/academics/history/fixtures';
import { problemWith } from '@/features/academics/testing';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import { labelled, setFieldValue } from '@/test/dom';
import {
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  ACTIVE_PLAN,
  FAMILY_FLAGGED,
  FAMILY_LIST,
  GUARDIAN,
  PLAN_ID,
  STUDENT,
  STUDENT_ID,
  TRUNCATED_LIST,
  TUTOR,
} from './fixtures';

// #310 E, A1, A2, G1 (family): the picker (default, ?student=, a foreign
// value falls back with no request for it, a single student, none), the
// consent message (from the picker and from a 403), the inline 404
// messages, empty and truncated, the rows linking to the family page, and
// the family detail without any action or select even when a fixture sets
// the flags.

const FAMILY = 'GET /api/v1/schedule/family-students';
const listRoute = (id: string) => `GET /api/v1/students/${id}/learning-plans`;
const DETAIL = `GET /api/v1/learning-plans/${PLAN_ID}`;
const FOREIGN = '00000000-0000-4000-8000-000000999999';

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');
const listCalls = () =>
  api.requests.filter((r) => r.path.endsWith('/learning-plans'));

function stub(routes: Routes = {}, as = GUARDIAN) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [FAMILY]: () => jsonResponse(200, FAMILY_STUDENTS),
    [listRoute(CHILD_ID)]: () => jsonResponse(200, FAMILY_LIST),
    [listRoute(SIBLING_ID)]: () =>
      jsonResponse(200, { ...FAMILY_LIST, items: [] }),
    [listRoute(STUDENT_ID)]: () => jsonResponse(200, FAMILY_LIST),
    [DETAIL]: () => jsonResponse(200, FAMILY_FLAGGED),
    ...routes,
  });
}

async function open(path: string, routes: Routes = {}, as = GUARDIAN) {
  stub(routes, as);
  app = await renderApp(path);
  await settleUntil(() => heading());
}

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
  it('a tutor-only user gets access-denied on /my-learning-plans, no request', async () => {
    await open('/my-learning-plans', {}, TUTOR);
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(FAMILY)).toHaveLength(0);
    expect(listCalls()).toHaveLength(0);
  });

  it('a tutor-only user gets access-denied on a family plan, no request', async () => {
    await open(`/my-learning-plans/${PLAN_ID}`, {}, TUTOR);
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(DETAIL)).toHaveLength(0);
  });
});

describe('the list (E1, E2, E4)', () => {
  it('defaults to the first student and lists their plans', async () => {
    await open('/my-learning-plans');
    await settleUntil(() => main().querySelectorAll('li').length === 2);
    expect(heading()!.textContent).toBe('Learning plans');
    const picker = labelled<HTMLSelectElement>(main(), 'Whose plans');
    expect(picker.value).toBe(CHILD_ID);
    expect([...picker.options].map((o) => o.textContent)).toEqual([
      'Lucía Pérez',
      'Andrés Pérez',
      'Mateo Pérez (not shared)',
    ]);
    expect(api.calls(listRoute(CHILD_ID))).toHaveLength(1);
    expect(main().querySelector('li a')!.getAttribute('href')).toBe(
      `/my-learning-plans/${FAMILY_LIST.items[0]!.id}`,
    );
    expect(text()).toContain('Created by Carmen Ruiz');
    expect(text()).not.toContain('New plan');
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('keeps the choice as ?student=', async () => {
    await open('/my-learning-plans');
    await settleUntil(() => main().querySelector('select'));
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Whose plans'),
      SIBLING_ID,
    );
    await settleUntil(
      () => app!.location() === `/my-learning-plans?student=${SIBLING_ID}`,
    );
    await settleUntil(() =>
      text().includes('No learning plans have been shared yet.'),
    );
  });

  it('opens the student of ?student=', async () => {
    await open(`/my-learning-plans?student=${SIBLING_ID}`);
    await settleUntil(() =>
      text().includes('No learning plans have been shared yet.'),
    );
    expect(api.calls(listRoute(CHILD_ID))).toHaveLength(0);
  });

  it('a foreign ?student= falls back to the first, with no request for it', async () => {
    await open(`/my-learning-plans?student=${FOREIGN}`);
    await settleUntil(() => main().querySelectorAll('li').length === 2);
    expect(listCalls().map((r) => r.path)).toEqual([
      `/api/v1/students/${CHILD_ID}/learning-plans`,
    ]);
  });

  it('a student: "My plans", no picker', async () => {
    await open(
      '/my-learning-plans',
      { [FAMILY]: () => jsonResponse(200, SELF_ONLY) },
      STUDENT,
    );
    await settleUntil(() => main().querySelectorAll('li').length === 2);
    expect(main().querySelector('select')).toBeNull();
    expect(text()).toContain('Whose plans My plans');
  });

  it('no linked student: the message', async () => {
    await open('/my-learning-plans', {
      [FAMILY]: () => jsonResponse(200, { items: [] }),
    });
    await settleUntil(() =>
      text().includes("There's no student linked to your account."),
    );
    expect(listCalls()).toHaveLength(0);
  });

  it('an adult who has not shared: the consent message, no request', async () => {
    await open(`/my-learning-plans?student=${ADULT_ID}`);
    await settleUntil(() =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
    expect(listCalls()).toHaveLength(0);
  });

  it('a 403 student_consent_required: the consent message', async () => {
    await open('/my-learning-plans', {
      [listRoute(CHILD_ID)]: () => problemWith(403, 'student_consent_required'),
    });
    await settleUntil(() =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
  });

  it('a 404: "These learning plans aren\'t available." and the picker stays', async () => {
    await open('/my-learning-plans', {
      [listRoute(CHILD_ID)]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() =>
      text().includes("These learning plans aren't available."),
    );
    expect(main().querySelector('select')).not.toBeNull();
  });

  it('truncated and error states', async () => {
    await open('/my-learning-plans', {
      [listRoute(CHILD_ID)]: () => jsonResponse(200, TRUNCATED_LIST),
    });
    await settleUntil(() => text().includes('Showing the latest 100 plans.'));
    app!.unmount();
    document.body.innerHTML = '';
    await open('/my-learning-plans', {
      [listRoute(CHILD_ID)]: () => problemWith(500, 'internal_error'),
    });
    await settleUntil(() => main().querySelector('[role="alert"]'));
    expect(text()).toContain('Try again');
  });
});

describe('the family detail (E3, E4)', () => {
  it('no action and no select, even with every flag set', async () => {
    await open(`/my-learning-plans/${PLAN_ID}`);
    await settleUntil(() => heading()?.textContent === ACTIVE_PLAN.title);
    expect(main().querySelectorAll('button')).toHaveLength(0);
    expect(main().querySelectorAll('select')).toHaveLength(0);
    expect(main().querySelector('a[href$="/edit"]')).toBeNull();
    expect(text()).toContain('1 of 3 milestones done · 33 %');
    expect(text()).toContain('Overdue');
    expect(text()).toContain('Pending');
    const back = main().querySelector(
      `a[href="/my-learning-plans?student=${STUDENT_ID}"]`,
    );
    expect(back?.textContent).toBe('Back to learning plans');
  });

  it('a 404: "This learning plan isn\'t available." with the link', async () => {
    await open(`/my-learning-plans/${PLAN_ID}`, {
      [DETAIL]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() =>
      text().includes("This learning plan isn't available."),
    );
    expect(main().querySelector('a[href="/my-learning-plans"]')).not.toBeNull();
    expect(heading()!.textContent).toBe('Learning plan');
  });

  it('a 403 student_consent_required: the consent message', async () => {
    await open(`/my-learning-plans/${PLAN_ID}`, {
      [DETAIL]: () => problemWith(403, 'student_consent_required'),
    });
    await settleUntil(() =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
  });
});

describe('navigation (A2)', () => {
  it('the guardian opens the list from the nav item', async () => {
    await open('/');
    const link = app!.container.querySelector<HTMLAnchorElement>(
      'header a[href="/my-learning-plans"]',
    );
    expect(link?.textContent).toBe('Learning plans');
  });
});
