import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  noContent,
  stubApi,
  type ApiStub,
  type Routes,
} from '@/test/api-stub';
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
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { LearningPlan } from './api';
import {
  ACTIVE_PLAN,
  ACTIVE_READ_ONLY,
  ALL_DONE_PLAN,
  COMPLETED_PLAN,
  DISCONTINUED_PLAN,
  DRAFT_PLAN,
  FINANCE,
  GUARDIAN,
  MANAGER,
  MANAGER_LIST,
  PLAN_ID,
  SCRIPT,
  SCRIPTED_PLAN,
  STUDENT,
  STUDENT_ID,
  TUTOR,
  TUTOR_GUARDIAN,
} from './fixtures';

// #310 C, A1, G1 (view): the actions strictly by the can_* flags (a holder
// on a draft, a tutor in another subject, a former tutor, active, completed,
// discontinued), each dialog's text and request body, focus back on the
// trigger after Escape or Cancel and on the status after a confirm, the
// progress select (one PATCH with the milestone's version, the cache
// replaced, reverted on error, "Progress saved."), the markers as text,
// markup as text, every C6 row with its focus, and the route access.

const DETAIL = `GET /api/v1/learning-plans/${PLAN_ID}`;
const TRANSITION = `POST /api/v1/learning-plans/${PLAN_ID}/transition`;
const DELETE = `DELETE /api/v1/learning-plans/${PLAN_ID}`;
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const LIST = `GET /api/v1/students/${STUDENT_ID}/learning-plans`;
const PAGE = `/learning-plans/${PLAN_ID}`;
const milestoneRoute = (id: string) =>
  `PATCH /api/v1/learning-plan-milestones/${id}`;
const RESUMEN = ACTIVE_PLAN.objectives[0]!.milestones[1]!;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const focused = () => document.activeElement;
const heading = () => main().querySelector('h1');
const buttons = () =>
  [...main().querySelectorAll('button')].map((b) => b.textContent);
const selects = () => main().querySelectorAll('select');
const status = (wanted: string) =>
  [...main().querySelectorAll<HTMLElement>('[role="status"]')].find(
    (element) => element.textContent === wanted,
  );

function stub(plan: LearningPlan, as = MANAGER, routes: Routes = {}) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [DETAIL]: () => jsonResponse(200, plan),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [LIST]: () => jsonResponse(200, MANAGER_LIST),
    ...routes,
  });
}

async function openPage(
  plan: LearningPlan,
  as = MANAGER,
  routes: Routes = {},
  path = PAGE,
) {
  stub(plan, as, routes);
  app = await renderApp(path);
  await settleUntil(() => heading()?.textContent === plan.title);
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
  it('Finance gets access-denied and no learning-plan request', async () => {
    stub(DRAFT_PLAN, FINANCE);
    app = await renderApp(PAGE);
    await settleUntil(() => heading());
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(
      api.requests.filter((r) => r.path.includes('learning-plan')),
    ).toHaveLength(0);
  });

  it.each([
    ['a guardian', GUARDIAN],
    ['a student', STUDENT],
  ] as const)(
    '%s goes to the family page of the plan, no staff request',
    async (_who, as) => {
      stub(ACTIVE_READ_ONLY, as);
      app = await renderApp(PAGE);
      await settleUntil(
        () => app!.location() === `/my-learning-plans/${PLAN_ID}`,
      );
      await settleUntil(() => heading()?.textContent === ACTIVE_PLAN.title);
      expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
      expect(buttons()).not.toContain('Edit');
      expect(selects()).toHaveLength(0);
    },
  );

  it('a tutor who is also a guardian keeps the staff view', async () => {
    await openPage(ACTIVE_PLAN, TUTOR_GUARDIAN);
    expect(app!.location()).toBe(PAGE);
    expect(buttons()).toContain('Complete');
  });

  it('a 404 shows the not-found page', async () => {
    stub(DRAFT_PLAN, MANAGER, {
      [DETAIL]: () => problemWith(404, 'not_found'),
    });
    app = await renderApp(PAGE);
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });
});

describe('content (C1-C3)', () => {
  it('shows the plan, its objectives and milestones with text markers', async () => {
    await openPage(ACTIVE_PLAN, TUTOR);
    expect(text()).toContain('Física');
    expect(text()).toContain('Active');
    expect(text()).toContain('Created by Carmen Ruiz');
    expect(text()).toContain('Activated on Nov 3, 2026');
    expect(text()).toContain('Nov 2, 2026 – Jan 29, 2027');
    expect(text()).toContain('1 of 3 milestones done · 33 %');
    expect(text()).toContain('0 of 2 objectives complete');
    expect(text()).toContain('1 overdue');
    const h2 = [...main().querySelectorAll('h2')].map((h) => h.textContent);
    expect(h2).toEqual(['1. Objetivo 1', '2. Objetivo 2']);
    expect(text()).toContain('1 of 2 milestones done · 50 %');
    expect(text()).toContain('Due Nov 5, 2026');
    expect(text()).toContain('Overdue');
    expect(text()).toContain('Done on Nov 8, 2026 by Carmen Ruiz');
    const progress = main().querySelector('progress')!;
    expect(progress.getAttribute('max')).toBe('3');
    expect(progress.getAttribute('value')).toBe('1');
    // Labelled by the visible text next to it.
    const label = document.getElementById(
      progress.getAttribute('aria-labelledby')!,
    );
    expect(label?.textContent).toBe('1 of 3 milestones done · 33 %');
    // The text is shown next to the bar, never hidden (B5).
    expect(label!.hidden).toBe(false);
    expect(label!.className).not.toMatch(/sr-only|hidden/);
    expect(label!.parentElement).toBe(progress.parentElement);
  });

  it('marks a complete objective and shows statuses as text when read-only', async () => {
    await openPage(COMPLETED_PLAN, TUTOR);
    expect(text()).toContain('Completed on Nov 20, 2026');
    expect(text()).toContain('Pending');
    expect(text()).toContain('In progress');
    expect(text()).not.toContain('Overdue'); // #332: closed plans have none
    expect(selects()).toHaveLength(0);
    await openPageAgain(ALL_DONE_PLAN);
    expect(text()).toMatch(/1\. Objetivo 1Complete/);
  });

  it('renders every free text as plain text', async () => {
    await openPage(SCRIPTED_PLAN);
    expect(main().querySelector('script')).toBeNull();
    expect(text().split(SCRIPT).length - 1).toBeGreaterThanOrEqual(5);
  });

  it('a draft without a target date', async () => {
    await openPage({ ...DRAFT_PLAN, target_on: null });
    expect(text()).toContain('From Nov 2, 2026 · No target date');
  });
});

async function openPageAgain(plan: LearningPlan, as = TUTOR) {
  app?.unmount();
  document.body.innerHTML = '';
  await openPage(plan, as);
}

describe('actions by the can_* flags (C5)', () => {
  it.each([
    [
      'a holder on a draft',
      DRAFT_PLAN,
      MANAGER,
      ['Activate', 'Discontinue', 'Delete draft'],
      true,
      false,
    ],
    [
      'a writer on an active plan',
      ACTIVE_PLAN,
      TUTOR,
      ['Complete', 'Discontinue'],
      true,
      true,
    ],
    [
      'a tutor teaching another subject',
      ACTIVE_READ_ONLY,
      TUTOR,
      [],
      false,
      false,
    ],
    [
      'a former tutor (read-only own draft)',
      { ...DRAFT_PLAN, ...readOnlyFlags() },
      TUTOR,
      [],
      false,
      false,
    ],
    [
      'a draft whose flags allow only Edit',
      { ...DRAFT_PLAN, ...readOnlyFlags(), can_edit: true },
      MANAGER,
      [],
      true,
      false,
    ],
    [
      'an active plan whose flags allow only progress',
      { ...ACTIVE_PLAN, ...readOnlyFlags(), can_update_progress: true },
      TUTOR,
      [],
      false,
      true,
    ],
    [
      'an active plan whose flags allow only Edit',
      { ...ACTIVE_PLAN, ...readOnlyFlags(), can_edit: true },
      MANAGER,
      [],
      true,
      false,
    ],
    ['a completed plan', COMPLETED_PLAN, MANAGER, [], false, false],
    ['a discontinued plan', DISCONTINUED_PLAN, MANAGER, [], false, false],
  ] as const)(
    '%s: exactly the allowed controls',
    async (_who, plan, as, actions, edit, progress) => {
      await openPage(plan, as);
      expect(
        buttons().filter((b) =>
          ['Activate', 'Complete', 'Discontinue', 'Delete draft'].includes(
            b ?? '',
          ),
        ),
      ).toEqual(actions);
      expect(Boolean(main().querySelector(`a[href="${PAGE}/edit"]`))).toBe(
        edit,
      );
      expect(selects().length > 0).toBe(progress);
    },
  );
});

function readOnlyFlags() {
  return {
    can_edit: false,
    can_activate: false,
    can_complete: false,
    can_discontinue: false,
    can_delete: false,
    can_update_progress: false,
  };
}

describe('dialogs (C5)', () => {
  it('Activate: the text names the student, sends the version, focuses the status', async () => {
    const activated = { ...ACTIVE_PLAN };
    await openPage(DRAFT_PLAN, MANAGER, {
      [TRANSITION]: () => jsonResponse(200, activated),
    });
    await settleUntil(() => api.calls(STUDENT_ROUTE).length > 0);
    await click(buttonNamed(main(), 'Activate'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Activate this plan?');
    expect(dialog.textContent).toContain(
      'José Ñúñez Pérez and guardians with access to their academic records will see this plan and get a notification.',
    );
    await click(buttonNamed(dialog, 'Activate'));
    expect(api.calls(TRANSITION)).toHaveLength(1);
    expect(api.calls(TRANSITION)[0]!.body).toEqual({
      version: 3,
      action: 'activate',
    });
    await settleUntil(() => openDialog() === null);
    await settleUntil(() => focused() === status('Plan activated.'));
    expect(text()).toContain('Active');
  });

  it('without the student name the text says "The student"', async () => {
    await openPage(DRAFT_PLAN, MANAGER, {
      [STUDENT_ROUTE]: () => problemWith(404, 'not_found'),
    });
    await click(buttonNamed(main(), 'Activate'));
    expect(openDialog()!.textContent).toContain(
      'The student and guardians with access to their academic records',
    );
  });

  it('Complete: the not-done count, then the request', async () => {
    await openPage(ACTIVE_PLAN, TUTOR, {
      [TRANSITION]: () => jsonResponse(200, COMPLETED_PLAN),
    });
    await settleUntil(() => api.calls(STUDENT_ROUTE).length > 0);
    await click(buttonNamed(main(), 'Complete'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Complete this plan?');
    expect(dialog.textContent).toContain(
      "It becomes read-only and can't be reopened. José Ñúñez Pérez and guardians are notified.",
    );
    expect(dialog.textContent).toContain(
      "2 milestones aren't done; they stay as they are.",
    );
    await click(buttonNamed(dialog, 'Complete'));
    expect(api.calls(TRANSITION)[0]!.body).toEqual({
      version: 5,
      action: 'complete',
    });
    await settleUntil(() => focused() === status('Plan completed.'));
    expect(buttons()).not.toContain('Complete');
  });

  it('Complete with every milestone done: no count', async () => {
    await openPage(ALL_DONE_PLAN, TUTOR);
    await click(buttonNamed(main(), 'Complete'));
    expect(openDialog()!.textContent).not.toMatch(/aren't done|isn't done/);
  });

  it.each([
    [
      'an active plan',
      ACTIVE_PLAN,
      'José Ñúñez Pérez and guardians still see it, marked Discontinued.',
    ],
    ['a draft', DRAFT_PLAN, 'It was never shared with the family.'],
  ] as const)('Discontinue %s: its variant', async (_what, plan, line) => {
    await openPage(plan, MANAGER, {
      [TRANSITION]: () => jsonResponse(200, DISCONTINUED_PLAN),
    });
    await settleUntil(() => api.calls(STUDENT_ROUTE).length > 0);
    await click(buttonNamed(main(), 'Discontinue'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      "Discontinue this plan?It becomes read-only and can't be reopened.",
    );
    expect(dialog.textContent).toContain(line);
    await click(buttonNamed(dialog, 'Discontinue'));
    expect(api.calls(TRANSITION)[0]!.body).toEqual({
      version: plan.version,
      action: 'discontinue',
    });
    await settleUntil(() => focused() === status('Plan discontinued.'));
  });

  it('Delete draft: DELETE with the version, then the list says "Draft deleted." (focused)', async () => {
    const urls: URL[] = [];
    await openPage(DRAFT_PLAN, MANAGER, {
      [DELETE]: (request) => {
        urls.push(new URL(request.url));
        return noContent();
      },
    });
    await click(buttonNamed(main(), 'Delete draft'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Delete this draft?');
    await click(buttonNamed(dialog, 'Delete draft'));
    expect(urls).toHaveLength(1);
    expect(urls[0]!.searchParams.get('version')).toBe('3');
    await settleUntil(
      () => app!.location() === `/students/${STUDENT_ID}/learning-plans`,
    );
    await settleUntil(() => focused() === status('Draft deleted.'));
    // Replace: Back doesn't return to the deleted plan.
    expect(app!.router.state.historyAction).toBe('REPLACE');
  });

  it.each([
    ['Activate', DRAFT_PLAN],
    ['Discontinue', DRAFT_PLAN],
    ['Delete draft', DRAFT_PLAN],
    ['Complete', ACTIVE_PLAN],
  ] as const)(
    '%s: Escape and Cancel return focus to the trigger, nothing sent',
    async (name, plan) => {
      await openPage(plan);
      for (const how of ['Escape', 'Cancel']) {
        const triggerButton = buttonNamed(main(), name);
        triggerButton.focus();
        await click(triggerButton);
        await settleUntil(() => openDialog());
        if (how === 'Escape') {
          await pressEscape();
        } else {
          await click(buttonNamed(openDialog()!, 'Cancel'));
        }
        await settleUntil(() => openDialog() === null);
        await settleUntil(() => focused() === buttonNamed(main(), name));
      }
      expect(api.calls(TRANSITION)).toHaveLength(0);
      expect(api.calls(DELETE)).toHaveLength(0);
    },
  );
});

describe('the progress select (C4)', () => {
  const select = () => labelled<HTMLSelectElement>(main(), 'Status of Resumen');

  it('sends one PATCH with the milestone version, replaces the cache, says so, keeps focus', async () => {
    const saved = {
      ...ACTIVE_PLAN,
      progress: { done: 2, total: 3, percent: '67' },
    };
    await openPage(ACTIVE_PLAN, TUTOR, {
      [milestoneRoute(RESUMEN.id)]: () => jsonResponse(200, saved),
    });
    expect(select().value).toBe('pending');
    select().focus();
    setFieldValue(select(), 'done');
    await settleUntil(() => api.calls(milestoneRoute(RESUMEN.id)).length === 1);
    expect(api.calls(milestoneRoute(RESUMEN.id))[0]!.body).toEqual({
      version: 4,
      status: 'done',
    });
    await settleUntil(() => text().includes('2 of 3 milestones done · 67 %'));
    await settleUntil(() => text().includes('Progress saved.'));
    await settleUntil(() => focused() === select());
    expect(select().disabled).toBe(false);
  });

  it.each([
    [
      'version_conflict',
      () => problemWith(409, 'version_conflict'),
      'This plan changed elsewhere.',
    ],
    [
      'a milestone gone (404)',
      () => problemWith(404, 'not_found'),
      'This milestone no longer exists.',
    ],
    [
      'plan_not_active',
      () => problemWith(409, 'plan_not_active'),
      "This plan isn't active.",
    ],
  ])(
    '%s: back to the previous value, the message focused, Reload focuses the heading',
    async (_what, answer, message) => {
      await openPage(ACTIVE_PLAN, TUTOR, {
        [milestoneRoute(RESUMEN.id)]: answer,
      });
      setFieldValue(select(), 'in_progress');
      await settleUntil(() => text().includes(message));
      expect(select().value).toBe('pending');
      const region = main().querySelector<HTMLElement>(
        `[data-milestone-failure="${RESUMEN.id}"]`,
      );
      await settleUntil(() => focused() === region);
      expect(select().getAttribute('aria-describedby')).toBe(region!.id);
      expect(text()).not.toContain('Progress saved.');
      const before = api.calls(DETAIL).length;
      await click(buttonNamed(main(), 'Reload'));
      await settleUntil(() => api.calls(DETAIL).length > before);
      await settleUntil(() => focused() === heading());
      expect(text()).not.toContain(message);
    },
  );

  it('is not shown without can_update_progress (a draft)', async () => {
    await openPage(DRAFT_PLAN);
    expect(selects()).toHaveLength(0);
  });
});

describe('refused transitions (C6)', () => {
  const failure = () => main().querySelector<HTMLElement>('div[tabindex="-1"]');

  it('409 version_conflict: the message (focused), Reload refetches and focuses the heading', async () => {
    await openPage(DRAFT_PLAN, MANAGER, {
      [TRANSITION]: () => problemWith(409, 'version_conflict'),
      [DETAIL]: [
        () => jsonResponse(200, DRAFT_PLAN),
        () => jsonResponse(200, { ...DRAFT_PLAN, version: 4 }),
      ],
    });
    await click(buttonNamed(main(), 'Activate'));
    await click(buttonNamed(openDialog()!, 'Activate'));
    await settleUntil(() => text().includes('This plan changed elsewhere.'));
    await settleUntil(() => focused() === failure());
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => api.calls(DETAIL).length === 2);
    await settleUntil(() => focused() === heading());
    expect(text()).not.toContain('This plan changed elsewhere.');
  });

  it('409 active_plan_exists: the subject and the link to the list', async () => {
    await openPage(DRAFT_PLAN, MANAGER, {
      [TRANSITION]: () => problemWith(409, 'active_plan_exists'),
    });
    await click(buttonNamed(main(), 'Activate'));
    await click(buttonNamed(openDialog()!, 'Activate'));
    await settleUntil(() =>
      text().includes(
        'Física already has an active plan for this student. Complete or discontinue it first.',
      ),
    );
    await settleUntil(() => focused() === failure());
    expect(
      failure()!.querySelector(
        `a[href="/students/${STUDENT_ID}/learning-plans"]`,
      ),
    ).not.toBeNull();
  });

  it.each([
    ['plan_closed', 409, "This plan is closed: it can't change anymore."],
    ['plan_not_draft', 409, "This plan isn't a draft anymore."],
    ['plan_not_active', 409, "This plan isn't active."],
    [
      'plan_subject_not_assigned',
      403,
      'You can change plans only in subjects you teach this student.',
    ],
    ['plan_read_only', 403, 'You can no longer change this plan.'],
  ] as const)(
    '%s: its message with Reload, focused',
    async (code, http, message) => {
      await openPage(DRAFT_PLAN, MANAGER, {
        [TRANSITION]: () => problemWith(http, code),
      });
      await click(buttonNamed(main(), 'Discontinue'));
      await click(buttonNamed(openDialog()!, 'Discontinue'));
      await settleUntil(() => text().includes(message));
      await settleUntil(() => focused() === failure());
      expect(buttons()).toContain('Reload');
    },
  );

  it('404 on a transition shows the not-found page', async () => {
    await openPage(DRAFT_PLAN, MANAGER, {
      [TRANSITION]: () => problemWith(404, 'not_found'),
    });
    await click(buttonNamed(main(), 'Activate'));
    await click(buttonNamed(openDialog()!, 'Activate'));
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });
});

describe('arriving from the editor', () => {
  it.each([
    ['draftSaved', 'Draft saved. Activate it to share it with the family.'],
    ['planSaved', 'Plan saved.'],
  ])('%s: the notice takes focus', async (notice, message) => {
    stub(DRAFT_PLAN);
    app = await renderApp('/');
    await settleUntil(() => main());
    await app.router.navigate(PAGE, { state: { notice } });
    await settleUntil(() => focused() === status(message));
  });
});
