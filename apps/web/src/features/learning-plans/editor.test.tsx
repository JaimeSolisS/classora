import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
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
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { LearningPlan } from './api';
import {
  ACTIVE_PLAN,
  DRAFT_PLAN,
  FULL_PLAN,
  MANAGER,
  MANAGER_LIST,
  PHYSICS,
  PLAN_ID,
  READ_ONLY_LIST,
  STUDENT_ID,
  TEN_MILESTONES_PLAN,
  TUTOR,
  TUTOR_LIST,
} from './fixtures';

// #310 D, G1 (editor): the create subject options (a tutor's
// creatable_subjects, a holder's GET /subjects, "(has an active plan)",
// can_create false), validation (focus on the first invalid field, nothing
// sent), add / remove / move with their focus and every disabled state with
// its reason (at 1, at 10 objectives, 10 milestones, 50 in the plan), the
// remove confirmations (Escape and Cancel back to the trigger), the active
// plan note, the POST and PUT bodies and the navigation (the notice
// focused), every D6 mapping with its focus, Reload after a conflict (ask
// first, then the heading), and the edit guard.

const LIST = `GET /api/v1/students/${STUDENT_ID}/learning-plans`;
const CREATE = `POST /api/v1/students/${STUDENT_ID}/learning-plans`;
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const SUBJECTS = 'GET /api/v1/subjects';
const DETAIL = `GET /api/v1/learning-plans/${PLAN_ID}`;
const PUT = `PUT /api/v1/learning-plans/${PLAN_ID}`;
const NEW_PAGE = `/students/${STUDENT_ID}/learning-plans/new`;
const EDIT_PAGE = `/learning-plans/${PLAN_ID}/edit`;
const VIEW = `/learning-plans/${PLAN_ID}`;
const MATHS_ID = '00000000-0000-4000-8000-000000310502';

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const focused = () => document.activeElement;
const heading = () => main().querySelector('h1');
const field = <
  T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
>(
  label: string,
) => labelled<T>(main(), label);
const button = (name: string) => buttonNamed(main(), name);
const status = (wanted: string) =>
  [...main().querySelectorAll<HTMLElement>('[role="status"]')].find(
    (element) => element.textContent === wanted,
  );
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
    [LIST]: () => jsonResponse(200, TUTOR_LIST),
    [SUBJECTS]: () =>
      jsonResponse(200, {
        items: [
          {
            id: PHYSICS.id,
            name: 'Física',
            education_level_name: 'Secundaria',
            is_active: true,
          },
          {
            id: MATHS_ID,
            name: 'Matemáticas',
            education_level_name: 'Primaria',
            is_active: true,
          },
        ],
        total: 2,
        limit: 100,
        offset: 0,
      }),
    [DETAIL]: () => jsonResponse(200, DRAFT_PLAN),
    ...routes,
  });
}

async function openNew(routes: Routes = {}, as = TUTOR) {
  stub(routes, as);
  app = await renderApp(NEW_PAGE);
  await settleUntil(() => main().querySelector('form'));
}

async function openEdit(plan: LearningPlan, routes: Routes = {}, as = TUTOR) {
  stub({ [DETAIL]: () => jsonResponse(200, plan), ...routes }, as);
  app = await renderApp(EDIT_PAGE);
  await settleUntil(() => main().querySelector('form'));
}

/** Fills the minimum of a valid new plan. */
function fillNew() {
  setFieldValue(field('Subject'), PHYSICS.id);
  setFieldValue(field('Title'), '  Leer mejor  ');
  setFieldValue(field('Title of objective 1'), 'Comprensión');
  setFieldValue(field('Title of milestone 1.1'), 'Lectura diaria');
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-12T18:00:00Z'));
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('create: the subject (D1)', () => {
  it('a tutor is offered their creatable_subjects only, marked when active', async () => {
    await openNew();
    const options = [...field<HTMLSelectElement>('Subject').options].map(
      (o) => o.textContent,
    );
    // Only Física (not Matemáticas of GET /subjects), labelled with its
    // level from GET /subjects.
    expect(options).toEqual([
      'Choose a subject',
      'Física (Secundaria) (has an active plan)',
    ]);
    expect(heading()!.textContent).toBe('New learning plan');
    expect(text()).toContain('For José Ñúñez Pérez');
  });

  it('a tutor gets plain names when GET /subjects fails', async () => {
    await openNew({ [SUBJECTS]: () => problemWith(500, 'internal_error') });
    const options = [...field<HTMLSelectElement>('Subject').options].map(
      (o) => o.textContent,
    );
    expect(options).toEqual([
      'Choose a subject',
      'Física (has an active plan)',
    ]);
  });

  it('a holder (null) is offered the active subjects of GET /subjects', async () => {
    await openNew({ [LIST]: () => jsonResponse(200, MANAGER_LIST) }, MANAGER);
    const options = [...field<HTMLSelectElement>('Subject').options].map(
      (o) => o.textContent,
    );
    expect(options).toEqual([
      'Choose a subject',
      'Física (Secundaria) (has an active plan)',
      'Matemáticas (Primaria)',
    ]);
    expect(api.calls(SUBJECTS)).toHaveLength(1);
  });

  it('can_create false: the message and no form', async () => {
    stub({ [LIST]: () => jsonResponse(200, READ_ONLY_LIST) });
    app = await renderApp(NEW_PAGE);
    await settleUntil(() =>
      text().includes("You can't create a learning plan for this student."),
    );
    expect(main().querySelector('form')).toBeNull();
  });

  it('a 404 on the student shows the not-found page', async () => {
    stub({ [STUDENT_ROUTE]: () => problemWith(404, 'not_found') });
    app = await renderApp(NEW_PAGE);
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });

  it('Starts on defaults to the center’s today', async () => {
    await openNew();
    expect(field<HTMLInputElement>('Starts on').value).toBe('2026-11-12');
    expect(field<HTMLInputElement>('Target date (optional)').min).toBe(
      '2026-11-12',
    );
  });
});

describe('validation (D2, D4)', () => {
  it('an empty form: field errors, focus on the first, nothing sent', async () => {
    await openNew();
    await click(button('Save plan'));
    await settleUntil(() => text().includes('Choose a subject.'));
    expect(text()).toContain('Write a title.');
    const subject = field<HTMLSelectElement>('Subject');
    expect(subject.getAttribute('aria-invalid')).toBe('true');
    expect(describedText(subject)).toBe('Choose a subject.');
    const milestoneTitle = field('Title of milestone 1.1');
    expect(milestoneTitle.getAttribute('aria-invalid')).toBe('true');
    await settleUntil(() => focused() === subject);
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('target_before_start lands on the target date, nothing sent', async () => {
    await openNew();
    fillNew();
    setFieldValue(field('Target date (optional)'), '2026-11-01');
    await click(button('Save plan'));
    const target = field<HTMLInputElement>('Target date (optional)');
    await settleUntil(() =>
      text().includes("The target date can't be before the start date."),
    );
    await settleUntil(() => focused() === target);
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('too long texts and an early due date', async () => {
    await openNew();
    fillNew();
    setFieldValue(field('Title'), 'x'.repeat(121));
    setFieldValue(field('Due date of milestone 1.1 (optional)'), '1999-12-31');
    await click(button('Save plan'));
    await settleUntil(() =>
      text().includes('The title can have at most 120 characters.'),
    );
    expect(text()).toContain('Choose a due date from January 1, 2000 on.');
    await settleUntil(() => focused() === field('Title'));
    expect(api.calls(CREATE)).toHaveLength(0);
  });
});

describe('objectives and milestones (D3)', () => {
  it('at 1: Remove is disabled with its reason; counters', async () => {
    await openNew();
    expect(text()).toContain('Objectives 1 of 10');
    expect(text()).toContain('1 of 50 milestones in this plan');
    expect(text()).toContain('Milestones 1 of 10');
    const removeObjective = button('Remove objective 1');
    expect(removeObjective.disabled).toBe(true);
    expect(describedText(removeObjective)).toBe(
      'A plan needs at least one objective.',
    );
    const removeMilestone = button('Remove milestone 1.1');
    expect(removeMilestone.disabled).toBe(true);
    expect(describedText(removeMilestone)).toBe(
      'An objective needs at least one milestone.',
    );
    expect(button('Move up objective 1').disabled).toBe(true);
    expect(button('Move down objective 1').disabled).toBe(true);
    expect(button('Add objective').disabled).toBe(false);
  });

  it('Add objective focuses its title; it brings one milestone', async () => {
    await openNew();
    await click(button('Add objective'));
    await settleUntil(() => focused() === field('Title of objective 2'));
    expect(text()).toContain('Objectives 2 of 10');
    expect(text()).toContain('2 of 50 milestones in this plan');
    expect(field('Title of milestone 2.1')).toBeTruthy();
  });

  it('Add milestone focuses its title; Remove focuses Add milestone', async () => {
    await openNew();
    setFieldValue(field('Title of objective 1'), 'Comprensión');
    await click(button('Add milestone to objective 1: Comprensión'));
    await settleUntil(() => focused() === field('Title of milestone 1.2'));
    expect(text()).toContain('Milestones 2 of 10');
    setFieldValue(field('Title of milestone 1.2'), 'Resumen');
    await click(button('Add milestone to objective 1: Comprensión'));
    expect(text()).toContain('3 of 50 milestones in this plan');
    await click(button('Remove milestone 1.3'));
    expect(text()).toContain('2 of 50 milestones in this plan');
    await settleUntil(
      () => focused() === button('Add milestone to objective 1: Comprensión'),
    );
    expect(text()).toContain('Milestone 1.3 removed.');
  });

  it('moves a milestone within its objective, focus on the moved one', async () => {
    await openNew();
    setFieldValue(field('Title of milestone 1.1'), 'Primero');
    await click(button('Add milestone to objective 1'));
    setFieldValue(field('Title of milestone 1.2'), 'Segundo');
    await click(button('Move down milestone 1.1: Primero'));
    expect(field<HTMLInputElement>('Title of milestone 1.1').value).toBe(
      'Segundo',
    );
    expect(field<HTMLInputElement>('Title of milestone 1.2').value).toBe(
      'Primero',
    );
    // At the end now: its "Move down" is disabled, so "Move up" has focus.
    await settleUntil(
      () => focused() === button('Move up milestone 1.2: Primero'),
    );
    expect(text()).toContain('Milestone moved to position 1.2.');
    // The first milestone of an objective can't go up, the last can't go
    // down: a milestone never leaves its objective.
    expect(button('Move up milestone 1.1: Segundo').disabled).toBe(true);
    expect(button('Move down milestone 1.2: Primero').disabled).toBe(true);
  });

  it('moves an objective with its milestones; Remove focuses Add objective', async () => {
    await openNew();
    setFieldValue(field('Title of objective 1'), 'Uno');
    setFieldValue(field('Title of milestone 1.1'), 'Hito uno');
    await click(button('Add objective'));
    setFieldValue(field('Title of objective 2'), 'Dos');
    setFieldValue(field('Title of milestone 2.1'), 'Hito dos');
    await click(button('Move up objective 2: Dos'));
    expect(field<HTMLInputElement>('Title of objective 1').value).toBe('Dos');
    expect(field<HTMLInputElement>('Title of milestone 1.1').value).toBe(
      'Hito dos',
    );
    expect(field<HTMLInputElement>('Title of milestone 2.1').value).toBe(
      'Hito uno',
    );
    await settleUntil(() => focused() === button('Move down objective 1: Dos'));
    await click(button('Remove objective 2: Uno'));
    await settleUntil(() => focused() === button('Add objective'));
    expect(text()).toContain('Objectives 1 of 10');
  });

  it('at 10 objectives and 50 milestones every add is disabled, with the reason', async () => {
    await openEdit(FULL_PLAN);
    const addObjective = button('Add objective');
    expect(addObjective.disabled).toBe(true);
    expect(describedText(addObjective)).toBe(
      'A plan can have at most 10 objectives.',
    );
    const adds = [
      ...main().querySelectorAll<HTMLButtonElement>('[data-add-milestone]'),
    ];
    expect(adds).toHaveLength(10);
    for (const add of adds) {
      expect(add.disabled).toBe(true);
      expect(describedText(add)).toBe('A plan can have at most 50 milestones.');
    }
    expect(text()).toContain('50 of 50 milestones in this plan');
  });

  it('at 10 milestones in an objective its add is disabled, with the reason', async () => {
    await openEdit(TEN_MILESTONES_PLAN);
    const add = main().querySelector<HTMLButtonElement>(
      '[data-add-milestone]',
    )!;
    expect(add.disabled).toBe(true);
    expect(describedText(add)).toBe(
      'An objective can have at most 10 milestones.',
    );
    expect(button('Add objective').disabled).toBe(false);
  });

  it('49 milestones: one more add, then the plan limit', async () => {
    const plan = {
      ...FULL_PLAN,
      objectives: FULL_PLAN.objectives.map((o, index) =>
        index === 9 ? { ...o, milestones: o.milestones.slice(0, 4) } : o,
      ),
    };
    await openEdit(plan);
    expect(button('Add objective').disabled).toBe(true);
    const lastAdd = () =>
      [
        ...main().querySelectorAll<HTMLButtonElement>('[data-add-milestone]'),
      ][9]!;
    expect(lastAdd().disabled).toBe(false);
    await click(lastAdd());
    expect(lastAdd().disabled).toBe(true);
    expect(describedText(lastAdd())).toBe(
      'A plan can have at most 50 milestones.',
    );
  });
});

describe('remove confirmations (D3)', () => {
  it.each(['Escape', 'Cancel'])(
    'removing an objective with saved milestones asks; %s returns focus to Remove',
    async (how) => {
      await openEdit(ACTIVE_PLAN);
      const remove = button('Remove objective 1: Objetivo 1');
      remove.focus();
      await click(remove);
      const dialog = openDialog()!;
      expect(dialog.textContent).toContain(
        'Progress already recorded for it will be lost.',
      );
      if (how === 'Escape') {
        await pressEscape();
      } else {
        await click(buttonNamed(dialog, 'Cancel'));
      }
      await settleUntil(() => openDialog() === null);
      await settleUntil(
        () => focused() === button('Remove objective 1: Objetivo 1'),
      );
      expect(text()).toContain('Objectives 2 of 10');
    },
  );

  it('confirming removes it and focuses Add objective', async () => {
    await openEdit(ACTIVE_PLAN);
    await click(button('Remove objective 1: Objetivo 1'));
    await click(buttonNamed(openDialog()!, 'Remove'));
    await settleUntil(() => openDialog() === null);
    expect(text()).toContain('Objectives 1 of 10');
    await settleUntil(() => focused() === button('Add objective'));
  });

  it('a done milestone asks; a pending saved one is removed at once', async () => {
    await openEdit(ACTIVE_PLAN);
    await click(button('Remove milestone 1.2: Resumen'));
    expect(openDialog()).toBeNull();
    expect(text()).toContain('Milestones 1 of 10');
    // The done milestone (now the only one of objective 1) can't be removed;
    // in objective 2 add one, and the done-ness rule: use a fresh plan.
    app!.unmount();
    document.body.innerHTML = '';
    await openEdit(ACTIVE_PLAN);
    const remove = button('Remove milestone 1.1: Lectura diaria');
    remove.focus();
    await click(remove);
    expect(openDialog()!.textContent).toContain(
      'Progress already recorded for it will be lost.',
    );
    await pressEscape();
    await settleUntil(
      () => focused() === button('Remove milestone 1.1: Lectura diaria'),
    );
    await click(button('Remove milestone 1.1: Lectura diaria'));
    await click(buttonNamed(openDialog()!, 'Remove'));
    await settleUntil(
      () => focused() === button('Add milestone to objective 1: Objetivo 1'),
    );
    expect(field<HTMLInputElement>('Title of milestone 1.1').value).toBe(
      'Resumen',
    );
  });
});

describe('save (D5)', () => {
  it('create: one POST, then the plan with "Draft saved." focused', async () => {
    const created = { ...DRAFT_PLAN };
    await openNew({ [CREATE]: () => jsonResponse(201, created) });
    fillNew();
    setFieldValue(field('Goal description'), ' Meta ');
    setFieldValue(field('Target date (optional)'), '2027-01-29');
    setFieldValue(field('Activities of milestone 1.1 (optional)'), '10 min');
    setFieldValue(field('Due date of milestone 1.1 (optional)'), '2026-12-01');
    await click(button('Save plan'));
    await settleUntil(() => app!.location() === VIEW);
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(api.calls(CREATE)[0]!.body).toEqual({
      subject_id: PHYSICS.id,
      title: 'Leer mejor',
      description: 'Meta',
      starts_on: '2026-11-12',
      target_on: '2027-01-29',
      objectives: [
        {
          title: 'Comprensión',
          description: null,
          milestones: [
            {
              title: 'Lectura diaria',
              activities: '10 min',
              due_on: '2026-12-01',
            },
          ],
        },
      ],
    });
    expect(app!.router.state.historyAction).toBe('REPLACE');
    const notice = 'Draft saved. Activate it to share it with the family.';
    await settleUntil(() => text().includes(notice));
    // #308 QA D1: the status message takes focus, not <body>.
    await settleUntil(() => focused() === status(notice));
  });

  it('edit: one PUT with the version and the ids, then "Plan saved." focused', async () => {
    await openEdit(DRAFT_PLAN, {
      [PUT]: () => jsonResponse(200, { ...DRAFT_PLAN, version: 4 }),
    });
    expect(text()).toContain("The subject can't change after creation.");
    expect(main().querySelector('select')).toBeNull();
    setFieldValue(field('Title'), 'Leer con más fluidez');
    await click(button('Add milestone to objective 2: Objetivo 2'));
    setFieldValue(field('Title of milestone 2.2'), 'Nuevo');
    await click(button('Save plan'));
    await settleUntil(() => app!.location() === VIEW);
    const body = api.calls(PUT)[0]!.body as {
      version: number;
      title: string;
      objectives: {
        id?: string;
        milestones: { id?: string; title: string }[];
      }[];
    };
    expect(api.calls(PUT)).toHaveLength(1);
    expect(body.version).toBe(3);
    expect(body.title).toBe('Leer con más fluidez');
    expect(body.objectives.map((o) => o.id)).toEqual(
      DRAFT_PLAN.objectives.map((o) => o.id),
    );
    expect(body.objectives[1]!.milestones).toEqual([
      {
        id: DRAFT_PLAN.objectives[1]!.milestones[0]!.id,
        title: 'Vocabulario',
        activities: null,
        due_on: null,
      },
      { title: 'Nuevo', activities: null, due_on: null },
    ]);
    expect(body).not.toHaveProperty('subject_id');
    await settleUntil(() => focused() === status('Plan saved.'));
  });

  it('an active plan: the note above Save, the progress as text', async () => {
    await openEdit(ACTIVE_PLAN);
    await settleUntil(() =>
      text().includes(
        'Changes are visible to José Ñúñez Pérez and guardians right away.',
      ),
    );
    expect(text()).toContain('Progress: Done');
    expect(text()).toContain('Progress: In progress');
  });

  it('a draft: no note', async () => {
    await openEdit(DRAFT_PLAN);
    expect(text()).not.toContain('Changes are visible');
  });
});

describe('refused saves (D6)', () => {
  async function refused(answer: () => Response) {
    await openEdit(DRAFT_PLAN, { [PUT]: answer });
    setFieldValue(field('Title'), 'Tecleado');
    await click(button('Save plan'));
    await settleUntil(() => api.calls(PUT).length === 1);
  }
  const failure = () => main().querySelector<HTMLElement>('div[tabindex="-1"]');

  it.each([
    [
      ['body', 'objectives', 1, 'milestones', 0, 'title'],
      'title_required',
      'Title of milestone 2.1',
      'Write a title.',
    ],
    [
      ['body', 'objectives', 0, 'milestones', 1, 'due_on'],
      'due_on_invalid',
      'Due date of milestone 1.2 (optional)',
      'Choose a due date from January 1, 2000 on.',
    ],
    [
      ['body', 'target_on'],
      'target_before_start',
      'Target date (optional)',
      "The target date can't be before the start date.",
    ],
    [
      ['body', 'objectives', 0, 'title'],
      'too_long',
      'Title of objective 1',
      'The title can have at most 200 characters.',
    ],
    [
      ['body', 'description'],
      'too_long',
      'Goal description',
      'The goal description can have at most 2000 characters.',
    ],
  ] as const)(
    '422 %j %s lands on its field, focused',
    async (path, code, label, message) => {
      await refused(() =>
        problemWith(422, 'validation_error', {
          errors: [{ path: [...path], code }],
        }),
      );
      await settleUntil(() => text().includes(message));
      const target = field(label);
      expect(target.getAttribute('aria-invalid')).toBe('true');
      await settleUntil(() => focused() === target);
      expect(field<HTMLInputElement>('Title').value).toBe('Tecleado');
    },
  );

  it('422 on the plan limit: the message, focus on Add objective', async () => {
    await refused(() =>
      problemWith(422, 'validation_error', {
        errors: [{ path: ['body', 'objectives'], code: 'milestones_too_many' }],
      }),
    );
    await settleUntil(() =>
      text().includes('A plan can have at most 50 milestones.'),
    );
    await settleUntil(() => focused() === button('Add objective'));
  });

  it("422 on an objective's milestones: focus on its Add milestone", async () => {
    await refused(() =>
      problemWith(422, 'validation_error', {
        errors: [
          {
            path: ['body', 'objectives', 1, 'milestones'],
            code: 'milestones_too_many',
          },
        ],
      }),
    );
    await settleUntil(() =>
      text().includes('An objective can have at most 10 milestones.'),
    );
    await settleUntil(
      () => focused() === button('Add milestone to objective 2: Objetivo 2'),
    );
  });

  it.each([
    ['objective_unknown', ['body', 'objectives', 0, 'id']],
    ['milestone_unknown', ['body', 'objectives', 0, 'milestones', 0, 'id']],
  ] as const)(
    '422 %s: "This plan changed elsewhere." focused',
    async (code, path) => {
      await refused(() =>
        problemWith(422, 'validation_error', {
          errors: [{ path: [...path], code }],
        }),
      );
      await settleUntil(() => text().includes('This plan changed elsewhere.'));
      await settleUntil(() => focused() === failure());
    },
  );

  it.each([
    ['plan_closed', 409, "This plan is closed: it can't change anymore."],
    [
      'plan_subject_not_assigned',
      403,
      'You can change plans only in subjects you teach this student.',
    ],
    ['plan_read_only', 403, 'You can no longer change this plan.'],
  ] as const)(
    '%s: its message, focused, typed values kept',
    async (code, http, message) => {
      await refused(() => problemWith(http, code));
      await settleUntil(() => text().includes(message));
      await settleUntil(() => focused() === failure());
      expect(field<HTMLInputElement>('Title').value).toBe('Tecleado');
    },
  );

  it('404 shows the not-found page', async () => {
    await refused(() => problemWith(404, 'not_found'));
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });

  describe('409 version_conflict and Reload', () => {
    async function conflict() {
      await openEdit(DRAFT_PLAN, {
        [PUT]: () => problemWith(409, 'version_conflict'),
        [DETAIL]: [
          () => jsonResponse(200, DRAFT_PLAN),
          () =>
            jsonResponse(200, {
              ...DRAFT_PLAN,
              version: 4,
              title: 'Cambiado en otra pestaña',
            }),
        ],
      });
      setFieldValue(field('Title'), 'Tecleado');
      await click(button('Save plan'));
      await settleUntil(() => text().includes('This plan changed elsewhere.'));
      await settleUntil(() => focused() === failure());
    }

    it.each(['Escape', 'Cancel'])(
      'Reload asks first; %s keeps the typing and returns focus to Reload',
      async (how) => {
        await conflict();
        const reload = button('Reload');
        reload.focus();
        await click(reload);
        const dialog = openDialog()!;
        expect(dialog.textContent).toContain('Discard your changes?');
        if (how === 'Escape') {
          await pressEscape();
        } else {
          await click(buttonNamed(dialog, 'Cancel'));
        }
        await settleUntil(() => openDialog() === null);
        await settleUntil(() => focused() === button('Reload'));
        expect(field<HTMLInputElement>('Title').value).toBe('Tecleado');
        expect(api.calls(DETAIL)).toHaveLength(1);
      },
    );

    it('confirmed: refetches, shows the new version, focuses the heading', async () => {
      await conflict();
      await click(button('Reload'));
      await click(buttonNamed(openDialog()!, 'Discard and reload'));
      await settleUntil(() => api.calls(DETAIL).length === 2);
      await settleUntil(
        () =>
          field<HTMLInputElement>('Title').value === 'Cambiado en otra pestaña',
      );
      // #308 QA D3/D4: focus after the form shows the new version.
      await settleUntil(() => focused() === heading());
      expect(text()).not.toContain('This plan changed elsewhere.');
      // The next save sends the new version.
      api.set({
        [PUT]: () => jsonResponse(200, { ...DRAFT_PLAN, version: 5 }),
      });
      await click(button('Save plan'));
      await settleUntil(() => api.calls(PUT).length === 2);
      expect((api.calls(PUT)[1]!.body as { version: number }).version).toBe(4);
    });
  });
});

describe('the edit guard (D7) and leaving (D8)', () => {
  it('without can_edit: the message, the link, no form', async () => {
    stub({
      [DETAIL]: () => jsonResponse(200, { ...DRAFT_PLAN, can_edit: false }),
    });
    app = await renderApp(EDIT_PAGE);
    await settleUntil(() => text().includes("This plan can't be edited."));
    expect(main().querySelector('form')).toBeNull();
    expect(main().querySelector(`a[href="${VIEW}"]`)?.textContent).toBe(
      'Back to the plan',
    );
  });

  it('leaving with unsaved changes asks first; Stay refocuses the link', async () => {
    await openEdit(DRAFT_PLAN);
    setFieldValue(field('Title'), 'Tecleado');
    const back = main().querySelector<HTMLAnchorElement>(`a[href="${VIEW}"]`)!;
    back.focus();
    await click(back);
    await settleUntil(() => openDialog());
    expect(openDialog()!.textContent).toContain('Leave without saving?');
    await click(buttonNamed(openDialog()!, 'Stay'));
    await settleUntil(() => openDialog() === null);
    expect(app!.location()).toBe(EDIT_PAGE);
    await settleUntil(() => focused() === back);
  });
});
