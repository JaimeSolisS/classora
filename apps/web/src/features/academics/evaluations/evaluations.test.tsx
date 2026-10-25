import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  noContent,
  stubApi,
  type ApiStub,
  type Routes,
} from '@/test/api-stub';
import {
  alerts,
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
import { ANY_SUBJECT, PHYSICS_ID, TEMPLATE } from '../templates/fixtures';
import { COORDINATOR, FINANCE, problemWith, TUTOR } from '../testing';
import {
  AMENDED,
  COMPLETE_DRAFT,
  CONTEXT,
  COURSE_CONTEXT,
  DRAFT,
  EVALUATION_ID,
  FINAL,
  FORMER_AUTHOR,
  OTHER_TUTORS,
  SCRIPT,
  SCRIPTED,
  SESSION_ID,
  STAFF_FINAL,
  STUDENT_ID,
  VOIDED,
} from './fixtures';
import { tutorAcademicLinks } from './rules';

// #308 C, D1, A1, G1 (evaluations): the new-evaluation form (the class's
// context, the template list by the class's subject, the subject rules,
// "Evaluated on", the course picker only for a course class, score
// validation, the create body and the page it goes to), one evaluation
// (PATCH with the version, the actions by the can_* flags, Finalize
// blocked while incomplete, the dialog's text, Amend with its internal
// reason, Delete and Void), every C8 row, plain text, focus after each
// dialog and action, and My schedule's "Academic history" / "Evaluate".

const STUDENT = `GET /api/v1/students/${STUDENT_ID}`;
const CONTEXT_ROUTE = `GET /api/v1/class-sessions/${SESSION_ID}/evaluation-context`;
const TEMPLATES = 'GET /api/v1/evaluation-templates';
const SUBJECTS = 'GET /api/v1/subjects';
const CREATE = 'POST /api/v1/evaluations';
const DETAIL = `GET /api/v1/evaluations/${EVALUATION_ID}`;
const PATCH = `PATCH /api/v1/evaluations/${EVALUATION_ID}`;
const FINALIZE = `POST /api/v1/evaluations/${EVALUATION_ID}/finalize`;
const VOID = `POST /api/v1/evaluations/${EVALUATION_ID}/void`;
const DELETE = `DELETE /api/v1/evaluations/${EVALUATION_ID}`;
const NEW_PATH = `/students/${STUDENT_ID}/evaluations/new`;
const PAGE = `/evaluations/${EVALUATION_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
let templateUrls: URL[] = [];
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const field = <
  T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
>(
  label: string,
  root: ParentNode = main(),
) => {
  // Score and Comment repeat per criterion: the first match.
  const found = [...root.querySelectorAll('label')].find(
    (candidate) => candidate.textContent === label,
  );
  if (!found) {
    return labelled<T>(root, label);
  }
  return document.getElementById(found.htmlFor) as T;
};
const scoreOf = (position: number) =>
  main().querySelector<HTMLInputElement>(
    `[data-score-position="${position}"]`,
  )!;
const focused = () => document.activeElement;

function stub(routes: Routes = {}, as = TUTOR): ApiStub {
  templateUrls = [];
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [STUDENT]: () => jsonResponse(200, JOSE_LIMITED),
    [CONTEXT_ROUTE]: () => jsonResponse(200, CONTEXT),
    [TEMPLATES]: (request) => {
      templateUrls.push(new URL(request.url));
      return jsonResponse(200, { items: [TEMPLATE, ANY_SUBJECT] });
    },
    [SUBJECTS]: () =>
      jsonResponse(200, {
        items: [
          {
            id: PHYSICS_ID,
            name: 'Física',
            education_level_name: 'Secundaria',
            is_active: true,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    [DETAIL]: () => jsonResponse(200, DRAFT),
    ...routes,
  });
  return api;
}

async function open(path: string, ready: () => unknown): Promise<void> {
  app = await renderApp(path);
  await settleUntil(ready);
}

async function openNew(path = NEW_PATH, routes: Routes = {}) {
  stub(routes);
  await open(path, () => main().querySelector('form'));
}

async function openPage(evaluation = DRAFT, routes: Routes = {}, as = TUTOR) {
  stub({ [DETAIL]: () => jsonResponse(200, evaluation), ...routes }, as);
  await open(PAGE, () =>
    main().querySelector('h1')?.textContent?.startsWith('Evaluation:'),
  );
}

function chooseTemplate(id = TEMPLATE.id) {
  setFieldValue(field<HTMLSelectElement>('Template'), id);
}

const buttons = () =>
  [...main().querySelectorAll('button')].map((b) => b.textContent);

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

describe('access (A1)', () => {
  it('Finance gets access-denied on an evaluation and no request', async () => {
    stub({}, FINANCE);
    await open(PAGE, () => main().querySelector('h1'));
    expect(text()).toMatch(/permission/i);
    expect(api.requests.filter((r) => r.path.includes('evaluation'))).toEqual(
      [],
    );
  });

  it('a staff-only user gets access-denied on the new-evaluation form', async () => {
    stub({}, COORDINATOR);
    await open(NEW_PATH, () => main().querySelector('h1'));
    expect(text()).toMatch(/permission/i);
    expect(api.calls(STUDENT)).toHaveLength(0);
  });
});

describe('the new-evaluation form (C1-C3)', () => {
  it('without a class: every active template, today, no course picker', async () => {
    await openNew();
    expect(main().querySelector('h1')?.textContent).toBe('New evaluation');
    expect(text()).toContain('José Ñúñez Pérez');
    expect(templateUrls.at(-1)!.searchParams.has('subject_id')).toBe(false);
    expect(api.calls(CONTEXT_ROUTE)).toHaveLength(0);
    const options = [
      ...field<HTMLSelectElement>('Template').querySelectorAll('option'),
    ].map((o) => o.textContent);
    expect(options).toEqual([
      'Choose a template',
      'Diagnóstico de física · Diagnostic · Física',
      'Seguimiento general · Ongoing · Any subject',
    ]);
    chooseTemplate();
    const date = field<HTMLInputElement>('Evaluated on');
    expect(date.value).toBe('2026-11-12');
    expect(date.max).toBe('2026-11-12');
    expect(
      [...main().querySelectorAll('label')].some(
        (l) => l.textContent === 'Course assessment',
      ),
    ).toBe(false);
    expect(text()).toContain('Only you can see this draft.');
    expect(text()).toContain('Families see the feedback once you finalize.');
  });

  it('with a class: its subject filters the templates and its date is the default', async () => {
    await openNew(`${NEW_PATH}?sessionId=${SESSION_ID}`);
    expect(templateUrls.at(-1)!.searchParams.get('subject_id')).toBe(
      PHYSICS_ID,
    );
    expect(text()).toContain('Class: Física');
    chooseTemplate(ANY_SUBJECT.id);
    expect(field<HTMLInputElement>('Evaluated on').value).toBe('2026-11-09');
    // An any-subject template takes the class's subject (text, no select).
    expect(
      [...main().querySelectorAll('select')].map(
        (s) => s.labels?.[0]?.textContent,
      ),
    ).toEqual(['Template']);
    expect(text()).toContain('Física');
  });

  it('the subject: the template’s, else a required select', async () => {
    await openNew();
    chooseTemplate();
    expect(
      [...main().querySelectorAll('select')].map(
        (s) => s.labels?.[0]?.textContent,
      ),
    ).toEqual(['Template']);
    chooseTemplate(ANY_SUBJECT.id);
    const subject = field<HTMLSelectElement>('Subject');
    await click(buttonNamed(main(), 'Save draft'));
    expect(text()).toContain('Choose a subject.');
    expect(subject.getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('shows the course picker only for a course class', async () => {
    await openNew(`${NEW_PATH}?sessionId=${SESSION_ID}`, {
      [CONTEXT_ROUTE]: () => jsonResponse(200, COURSE_CONTEXT),
    });
    chooseTemplate();
    const picker = field<HTMLSelectElement>('Course assessment');
    expect([...picker.options].map((o) => o.textContent)).toEqual([
      'None',
      'Examen parcial (Required)',
      'Práctica de laboratorio',
    ]);
  });

  it.each([
    ['a 404', { [CONTEXT_ROUTE]: () => problemWith(404, 'not_found') }],
    [
      'a class that has not started',
      {
        [CONTEXT_ROUTE]: () =>
          jsonResponse(200, { ...CONTEXT, evaluable: false }),
      },
    ],
    [
      'a student who is not in the class',
      {
        [CONTEXT_ROUTE]: () =>
          jsonResponse(200, { ...CONTEXT, participants: [] }),
      },
    ],
  ])(
    '%s: "This class can\'t be evaluated." and nothing is sent',
    async (_name, routes) => {
      stub(routes);
      await open(`${NEW_PATH}?sessionId=${SESSION_ID}`, () =>
        text().includes("This class can't be evaluated."),
      );
      const link = [...main().querySelectorAll('a')].find(
        (a) => a.textContent === 'Record an evaluation without a class',
      );
      expect(link?.getAttribute('href')).toBe(NEW_PATH);
      expect(main().querySelector('form')).toBeNull();
      expect(api.calls(TEMPLATES)).toHaveLength(0);
    },
  );

  it('without templates: the message and no form', async () => {
    stub({ [TEMPLATES]: () => jsonResponse(200, { items: [] }) });
    await open(NEW_PATH, () =>
      text().includes('No evaluation templates are available.'),
    );
    expect(main().querySelector('form')).toBeNull();
  });

  it('a 404 on the student shows the not-found page', async () => {
    stub({ [STUDENT]: () => problemWith(404, 'not_found') });
    await open(
      NEW_PATH,
      () => main().querySelector('h1')?.textContent === 'Page not found',
    );
  });

  it('checks the scores, the date and the template', async () => {
    await openNew();
    await click(buttonNamed(main(), 'Save draft'));
    expect(text()).toContain('Choose a template.');
    chooseTemplate();
    setFieldValue(scoreOf(1), '10.5');
    setFieldValue(scoreOf(2), '7,5');
    setFieldValue(field<HTMLInputElement>('Evaluated on'), '2026-11-13');
    await click(buttonNamed(main(), 'Save draft'));
    expect(text()).toContain(
      "The score must be from 0 to the criterion's maximum.",
    );
    expect(text()).toContain('Use a period for decimals, for example 7.5.');
    expect(text()).toContain("The date can't be after today.");
    await settleUntil(() => focused() === field('Evaluated on'));
    setFieldValue(scoreOf(2), '9.555');
    await click(buttonNamed(main(), 'Save draft'));
    expect(text()).toContain('Use at most two decimal places.');
    expect(scoreOf(2).getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('sends the create body and goes to the new evaluation', async () => {
    await openNew(`${NEW_PATH}?sessionId=${SESSION_ID}`, {
      [CONTEXT_ROUTE]: () => jsonResponse(200, COURSE_CONTEXT),
      [CREATE]: () => jsonResponse(201, DRAFT),
    });
    chooseTemplate(ANY_SUBJECT.id);
    setFieldValue(
      field<HTMLSelectElement>('Course assessment'),
      COURSE_CONTEXT.course!.assessments[0]!.id,
    );
    setFieldValue(scoreOf(1), '4');
    setFieldValue(field<HTMLTextAreaElement>('Comment'), '  Participa  ');
    setFieldValue(field<HTMLTextAreaElement>('Feedback'), ' Sigue así ');
    await click(buttonNamed(main(), 'Save draft'));
    await settleUntil(() => app!.location() === PAGE);
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(api.calls(CREATE)[0]!.body).toEqual({
      student_id: STUDENT_ID,
      template_id: ANY_SUBJECT.id,
      subject_id: PHYSICS_ID,
      evaluated_on: '2026-11-09',
      class_session_id: SESSION_ID,
      course_assessment_id: COURSE_CONTEXT.course!.assessments[0]!.id,
      scores: [{ position: 1, score: '4.00', comment: 'Participa' }],
      feedback: 'Sigue así',
    });
    await settleUntil(() => text().includes('Draft saved.'));
  });

  it('a template with a subject sends no subject_id', async () => {
    await openNew(NEW_PATH, { [CREATE]: () => jsonResponse(201, DRAFT) });
    chooseTemplate();
    await click(buttonNamed(main(), 'Save draft'));
    await settleUntil(() => api.calls(CREATE).length === 1);
    const body = api.calls(CREATE)[0]!.body as Record<string, unknown>;
    expect(body).not.toHaveProperty('subject_id');
    expect(body).not.toHaveProperty('class_session_id');
    expect(body.scores).toEqual([
      { position: 1, score: null, comment: null },
      { position: 2, score: null, comment: null },
    ]);
  });

  it.each([
    [
      '422 template_unknown',
      () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'template_id'], code: 'template_unknown' }],
        }),
    ],
    ['409 template_inactive', () => problemWith(409, 'template_inactive')],
  ])(
    '%s: on the template select, and the list reloads',
    async (_name, response) => {
      await openNew(NEW_PATH, { [CREATE]: response });
      chooseTemplate();
      const before = api.calls(TEMPLATES).length;
      await click(buttonNamed(main(), 'Save draft'));
      await settleUntil(() =>
        text().includes('This template is no longer available.'),
      );
      expect(field('Template').getAttribute('aria-invalid')).toBe('true');
      await settleUntil(() => api.calls(TEMPLATES).length > before);
    },
  );

  it.each([
    [
      '422 class_session_invalid',
      () =>
        problemWith(422, 'validation_error', {
          errors: [
            {
              path: ['body', 'class_session_id'],
              code: 'class_session_invalid',
            },
          ],
        }),
    ],
    [
      '409 session_not_evaluable',
      () =>
        problemWith(409, 'session_not_evaluable', {
          details: { state: 'offered' },
        }),
    ],
  ])('%s: the class message and the link', async (_name, response) => {
    await openNew(`${NEW_PATH}?sessionId=${SESSION_ID}`, {
      [CREATE]: response,
    });
    chooseTemplate();
    setFieldValue(scoreOf(1), '5');
    await click(buttonNamed(main(), 'Save draft'));
    await settleUntil(() =>
      alerts(main()).some((a) => a.includes("can't be evaluated")),
    );
    expect(scoreOf(1).value).toBe('5');
    const region = main().querySelector<HTMLElement>(
      '[tabindex="-1"] [role="alert"]',
    )!.parentElement!;
    await settleUntil(() => focused() === region);
  });

  it('server field errors land on their fields by path', async () => {
    await openNew(NEW_PATH, {
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            {
              path: ['body', 'scores', 1, 'score'],
              code: 'score_out_of_range',
            },
            { path: ['body', 'evaluated_on'], code: 'evaluated_on_invalid' },
          ],
        }),
    });
    chooseTemplate();
    setFieldValue(scoreOf(2), '3');
    await click(buttonNamed(main(), 'Save draft'));
    await settleUntil(() => scoreOf(2).getAttribute('aria-invalid') === 'true');
    expect(text()).toContain('Choose a valid date.');
    await settleUntil(() => focused() === field('Evaluated on'));
  });
});

describe('leaving with unsaved changes (C9)', () => {
  it('asks first; Escape stays and refocuses the link, Leave goes', async () => {
    await openNew();
    chooseTemplate();
    const back = [...main().querySelectorAll<HTMLAnchorElement>('a')].find(
      (a) => a.textContent === 'Back to academic history',
    )!;
    back.focus();
    await click(back);
    await settleUntil(() => openDialog());
    expect(openDialog()!.textContent).toContain('Leave without saving?');
    expect(app!.location()).toBe(NEW_PATH);
    await pressEscape();
    await settleUntil(() => openDialog() === null);
    expect(app!.location()).toBe(NEW_PATH);
    await settleUntil(() => focused() === back);
    await click(back);
    await settleUntil(() => openDialog());
    await click(buttonNamed(openDialog()!, 'Leave'));
    await settleUntil(
      () => app!.location() === `/students/${STUDENT_ID}/history`,
    );
  });

  it('a clean form leaves without asking', async () => {
    await openNew();
    const back = [...main().querySelectorAll<HTMLAnchorElement>('a')].find(
      (a) => a.textContent === 'Back to academic history',
    )!;
    await click(back);
    await settleUntil(
      () => app!.location() === `/students/${STUDENT_ID}/history`,
    );
    expect(openDialog()).toBeNull();
  });
});

describe('one evaluation (C4-C7)', () => {
  it('shows the status badges as text', async () => {
    for (const [evaluation, badge] of [
      [DRAFT, 'Draft'],
      [FINAL, 'Final'],
      [AMENDED, 'Final · Updated'],
      [VOIDED, 'Voided'],
    ] as const) {
      await openPage(evaluation);
      expect(text()).toContain(badge);
      app!.unmount();
      app = undefined;
      vi.unstubAllGlobals();
    }
  });

  it.each([
    ['the draft author', DRAFT, ['Save draft', 'Finalize', 'Delete draft']],
    ['the final author', FINAL, ['Amend']],
    ['another tutor', OTHER_TUTORS, []],
    ['a former author', FORMER_AUTHOR, []],
    ['staff with can_void', STAFF_FINAL, ['Void']],
  ] as const)('%s gets exactly %j', async (_who, evaluation, expected) => {
    await openPage(evaluation);
    const actions = buttons().filter((b) =>
      [
        'Save draft',
        'Finalize',
        'Delete draft',
        'Amend',
        'Void',
        'Save changes',
      ].includes(b ?? ''),
    );
    expect(actions).toEqual(expected);
  });

  it('a read-only final evaluation shows its numbers as text', async () => {
    await openPage(OTHER_TUTORS);
    expect(main().querySelector('form')).toBeNull();
    expect(text()).toContain('Total 17.50 of 20.00 (87.50 %)');
    expect(text()).toContain('8.00 of 10.00');
    expect(text()).toContain('Bien explicado.');
    expect(text()).toContain('Muy buen trabajo.');
    expect(text()).toContain('By Pedro Gómez');
    expect(text()).toContain('Finalized on');
  });

  it('the internal reason and the void line', async () => {
    await openPage(AMENDED);
    expect(text()).toContain(
      'Last change reason (internal): Corregí una suma.',
    );
    expect(text()).toContain('Updated on');
    app!.unmount();
    app = undefined;
    await openPage(VOIDED);
    expect(text()).toContain(
      'Voided on 12 Nov 2026 by Demo Coordinator: Se registró al estudiante equivocado.'.replace(
        '12 Nov 2026',
        'Nov 12, 2026',
      ),
    );
  });

  it('renders every free text as plain text', async () => {
    await openPage(SCRIPTED);
    expect(main().querySelector('script')).toBeNull();
    const shown = text().split(SCRIPT).length - 1;
    // Feedback, the comment and the internal reason.
    expect(shown).toBe(3);
  });

  it('saves a draft with PATCH and the version, then focuses the notice', async () => {
    await openPage(DRAFT, {
      [PATCH]: () => jsonResponse(200, { ...DRAFT, version: 2 }),
    });
    setFieldValue(scoreOf(2), '6.5');
    await click(buttonNamed(main(), 'Save draft'));
    await settleUntil(() => api.calls(PATCH).length === 1);
    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 1,
      evaluated_on: '2026-11-09',
      scores: [
        { position: 1, score: '8.00', comment: null },
        { position: 2, score: '6.50', comment: null },
      ],
      feedback: null,
    });
    await settleUntil(() => text().includes('Draft saved.'));
    const notice = main().querySelector<HTMLElement>('[role="status"]');
    await settleUntil(() => focused() === notice);
  });

  describe('Finalize (C5)', () => {
    it('is blocked while a criterion is unscored: lists it, focuses its score', async () => {
      await openPage(DRAFT);
      await click(buttonNamed(main(), 'Finalize'));
      expect(openDialog()).toBeNull();
      const alert = alerts(main()).join(' ');
      expect(alert).toContain('Score these criteria before finalizing:');
      expect(alert).toContain('2. Resolución');
      expect(alert).not.toContain('1. Comprensión');
      await settleUntil(() => focused() === scoreOf(2));
      expect(api.calls(FINALIZE)).toHaveLength(0);
    });

    it('saves pending changes first, then confirms with the version', async () => {
      await openPage(DRAFT, {
        [PATCH]: () => jsonResponse(200, { ...COMPLETE_DRAFT, version: 2 }),
        [FINALIZE]: () => jsonResponse(200, { ...FINAL, version: 3 }),
      });
      setFieldValue(scoreOf(2), '9.5');
      await click(buttonNamed(main(), 'Finalize'));
      await settleUntil(() => openDialog());
      expect(api.calls(PATCH)).toHaveLength(1);
      expect(openDialog()!.textContent).toContain(
        'José Ñúñez Pérez and guardians with access to their academic records will see this evaluation.',
      );
      await click(buttonNamed(openDialog()!, 'Finalize'));
      await settleUntil(() => api.calls(FINALIZE).length === 1);
      expect(api.calls(FINALIZE)[0]!.body).toEqual({ version: 2 });
      await settleUntil(() => text().includes('Evaluation finalized.'));
      await settleUntil(
        () => focused() === main().querySelector('[role="status"]'),
      );
      expect(text()).toContain('Total 17.50 of 20.00 (87.50 %)');
    });

    it('sends no PATCH when nothing changed', async () => {
      await openPage(COMPLETE_DRAFT);
      await click(buttonNamed(main(), 'Finalize'));
      await settleUntil(() => openDialog());
      expect(api.calls(PATCH)).toHaveLength(0);
    });

    it.each(['Escape', 'Cancel'])(
      'returns focus to Finalize after %s',
      async (how) => {
        await openPage(COMPLETE_DRAFT);
        const finalize = buttonNamed(main(), 'Finalize');
        finalize.focus();
        await click(finalize);
        await settleUntil(() => openDialog());
        if (how === 'Escape') {
          await pressEscape();
        } else {
          await click(buttonNamed(openDialog()!, 'Cancel'));
        }
        await settleUntil(() => openDialog() === null);
        await settleUntil(() => focused() === buttonNamed(main(), 'Finalize'));
        expect(api.calls(FINALIZE)).toHaveLength(0);
      },
    );

    it("maps the server's evaluation_incomplete to the same alert", async () => {
      await openPage(COMPLETE_DRAFT, {
        [FINALIZE]: () =>
          problemWith(409, 'evaluation_incomplete', {
            details: { missing_positions: ['1'] },
          }),
      });
      await click(buttonNamed(main(), 'Finalize'));
      await settleUntil(() => openDialog());
      await click(buttonNamed(openDialog()!, 'Finalize'));
      await settleUntil(() =>
        alerts(main()).some((a) => a.includes('1. Comprensión')),
      );
      await settleUntil(() => focused() === scoreOf(1));
    });
  });

  describe('Amend (C5)', () => {
    async function startAmend(routes: Routes = {}) {
      await openPage(FINAL, routes);
      await click(buttonNamed(main(), 'Amend'));
    }

    it('requires the reason and says it stays internal', async () => {
      await startAmend();
      expect(text()).toContain(
        'The reason stays internal: the student and guardians never see it.',
      );
      await settleUntil(() => focused() === scoreOf(1));
      await click(buttonNamed(main(), 'Save changes'));
      expect(text()).toContain('Write a reason.');
      expect(api.calls(PATCH)).toHaveLength(0);
      setFieldValue(scoreOf(2), '');
      setFieldValue(
        field<HTMLTextAreaElement>('Reason for the change'),
        'Error de captura',
      );
      await click(buttonNamed(main(), 'Save changes'));
      expect(text()).toContain('Every criterion needs a score.');
      expect(api.calls(PATCH)).toHaveLength(0);
    });

    it('sends the PATCH with the version and change_reason', async () => {
      await startAmend({
        [PATCH]: () => jsonResponse(200, AMENDED),
      });
      setFieldValue(scoreOf(1), '9');
      setFieldValue(
        field<HTMLTextAreaElement>('Reason for the change'),
        '  Corregí una suma.  ',
      );
      await click(buttonNamed(main(), 'Save changes'));
      await settleUntil(() => api.calls(PATCH).length === 1);
      expect(api.calls(PATCH)[0]!.body).toEqual({
        version: 3,
        evaluated_on: '2026-11-09',
        scores: [
          { position: 1, score: '9.00', comment: 'Bien explicado.' },
          { position: 2, score: '9.50', comment: null },
        ],
        feedback: 'Muy buen trabajo.',
        change_reason: 'Corregí una suma.',
      });
      await settleUntil(() => text().includes('Changes saved.'));
      expect(text()).toContain('Final · Updated');
    });

    it("puts the server's change_reason_required on the reason", async () => {
      await startAmend({
        [PATCH]: () =>
          problemWith(422, 'validation_error', {
            errors: [
              {
                path: ['body', 'change_reason'],
                code: 'change_reason_required',
              },
            ],
          }),
      });
      setFieldValue(
        field<HTMLTextAreaElement>('Reason for the change'),
        'Motivo',
      );
      await click(buttonNamed(main(), 'Save changes'));
      await settleUntil(() => api.calls(PATCH).length === 1);
      await settleUntil(
        () =>
          field('Reason for the change').getAttribute('aria-invalid') ===
          'true',
      );
      expect(text()).toContain('Write a reason.');
      await settleUntil(() => focused() === field('Reason for the change'));
    });

    it('Cancel with nothing typed returns to the read view and to Amend', async () => {
      await startAmend();
      await click(buttonNamed(main(), 'Cancel'));
      expect(main().querySelector('form')).toBeNull();
      await settleUntil(() => focused() === buttonNamed(main(), 'Amend'));
    });

    it.each(['Escape', 'Keep editing'])(
      'Cancel after typing asks first; %s keeps editing and refocuses Cancel',
      async (how) => {
        await startAmend();
        setFieldValue(
          field<HTMLTextAreaElement>('Reason for the change'),
          'algo',
        );
        const cancel = buttonNamed(main(), 'Cancel');
        cancel.focus();
        await click(cancel);
        await settleUntil(() => openDialog());
        if (how === 'Escape') {
          await pressEscape();
        } else {
          await click(buttonNamed(openDialog()!, 'Keep editing'));
        }
        await settleUntil(() => openDialog() === null);
        expect(main().querySelector('form')).not.toBeNull();
        await settleUntil(() => focused() === buttonNamed(main(), 'Cancel'));
      },
    );

    it('Discard changes restores the read view', async () => {
      await startAmend();
      setFieldValue(scoreOf(1), '1');
      await click(buttonNamed(main(), 'Cancel'));
      await settleUntil(() => openDialog());
      await click(buttonNamed(openDialog()!, 'Discard changes'));
      await settleUntil(() => main().querySelector('form') === null);
      expect(text()).toContain('8.00 of 10.00');
      await settleUntil(() => focused() === buttonNamed(main(), 'Amend'));
    });
  });

  describe('Delete draft (C5)', () => {
    it('confirms, sends the version and goes to the Evaluations tab', async () => {
      await openPage(DRAFT, { [DELETE]: () => noContent() });
      await click(buttonNamed(main(), 'Delete draft'));
      await settleUntil(() => openDialog());
      expect(openDialog()!.textContent).toContain('Delete this draft?');
      await click(buttonNamed(openDialog()!, 'Delete draft'));
      await settleUntil(() =>
        app!
          .location()
          .startsWith(`/students/${STUDENT_ID}/history?tab=evaluations`),
      );
      const [call] = api.requests.filter((r) => r.method === 'DELETE');
      expect(call!.path).toBe(`/api/v1/evaluations/${EVALUATION_ID}`);
      await settleUntil(() => text().includes('Draft deleted.'));
    });

    it.each(['Escape', 'Cancel'])(
      'returns focus to Delete draft after %s',
      async (how) => {
        await openPage(DRAFT);
        const trigger = buttonNamed(main(), 'Delete draft');
        trigger.focus();
        await click(trigger);
        await settleUntil(() => openDialog());
        if (how === 'Escape') {
          await pressEscape();
        } else {
          await click(buttonNamed(openDialog()!, 'Cancel'));
        }
        await settleUntil(() => openDialog() === null);
        await settleUntil(
          () => focused() === buttonNamed(main(), 'Delete draft'),
        );
        expect(api.requests.filter((r) => r.method === 'DELETE')).toEqual([]);
      },
    );
  });

  describe('Void (C7)', () => {
    it('requires a reason, sends it with the version, focuses the heading', async () => {
      await openPage(
        STAFF_FINAL,
        { [VOID]: () => jsonResponse(200, VOIDED) },
        COORDINATOR,
      );
      await click(buttonNamed(main(), 'Void'));
      await settleUntil(() => openDialog());
      const dialog = openDialog()!;
      expect(dialog.textContent).toContain('Void this evaluation');
      expect(dialog.textContent).toContain(
        "It disappears from the academic history for the student, guardians and tutors. This can't be undone.",
      );
      await click(buttonNamed(dialog, 'Void evaluation'));
      expect(dialog.textContent).toContain('Write a reason.');
      expect(api.calls(VOID)).toHaveLength(0);
      const reason = labelled<HTMLTextAreaElement>(dialog, 'Reason');
      expect(focused()).toBe(reason);
      setFieldValue(reason, '  Estudiante equivocado  ');
      await click(buttonNamed(dialog, 'Void evaluation'));
      await settleUntil(() => api.calls(VOID).length === 1);
      expect(api.calls(VOID)[0]!.body).toEqual({
        version: 3,
        reason: 'Estudiante equivocado',
      });
      await settleUntil(() => openDialog() === null);
      expect(text()).toContain('Evaluation voided.');
      expect(text()).toContain('Voided');
      await settleUntil(() => focused() === main().querySelector('h1'));
    });

    it("puts the server's reason_required on the field", async () => {
      await openPage(
        STAFF_FINAL,
        {
          [VOID]: () =>
            problemWith(422, 'validation_error', {
              errors: [{ path: ['body', 'reason'], code: 'reason_required' }],
            }),
        },
        COORDINATOR,
      );
      await click(buttonNamed(main(), 'Void'));
      await settleUntil(() => openDialog());
      setFieldValue(
        labelled<HTMLTextAreaElement>(openDialog()!, 'Reason'),
        'x',
      );
      await click(buttonNamed(openDialog()!, 'Void evaluation'));
      await settleUntil(() =>
        openDialog()?.textContent?.includes('Write a reason.'),
      );
    });

    it.each(['Escape', 'Cancel'])(
      'returns focus to Void after %s',
      async (how) => {
        await openPage(STAFF_FINAL, {}, COORDINATOR);
        const trigger = buttonNamed(main(), 'Void');
        trigger.focus();
        await click(trigger);
        await settleUntil(() => openDialog());
        if (how === 'Escape') {
          await pressEscape();
        } else {
          await click(buttonNamed(openDialog()!, 'Cancel'));
        }
        await settleUntil(() => openDialog() === null);
        await settleUntil(() => focused() === buttonNamed(main(), 'Void'));
      },
    );
  });
});

describe('server errors (C8)', () => {
  async function refusedSave(response: () => Response) {
    await openPage(DRAFT, { [PATCH]: response });
    setFieldValue(scoreOf(2), '7');
    await click(buttonNamed(main(), 'Save draft'));
    await settleUntil(() => api.calls(PATCH).length === 1);
    await settle();
  }

  const failureRegion = () =>
    main().querySelector<HTMLElement>('div[tabindex="-1"]');

  it('409 version_conflict: the message, Reload, focus on it', async () => {
    await refusedSave(() => problemWith(409, 'version_conflict'));
    await settleUntil(() =>
      text().includes('This evaluation changed elsewhere.'),
    );
    await settleUntil(() => focused() === failureRegion());
    expect(scoreOf(2).value).toBe('7');
    const before = api.calls(DETAIL).length;
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => api.calls(DETAIL).length > before);
    await settleUntil(() => scoreOf(2).value === '');
  });

  it.each([
    [
      'score_out_of_range',
      "The score must be from 0 to the criterion's maximum.",
    ],
    ['score_precision', 'Use at most two decimal places.'],
    ['score_required', 'Every criterion needs a score.'],
  ])('422 %s lands on the score', async (code, message) => {
    await refusedSave(() =>
      problemWith(422, 'validation_error', {
        errors: [{ path: ['body', 'scores', 1, 'score'], code }],
      }),
    );
    await settleUntil(() => text().includes(message));
    await settleUntil(() => focused() === scoreOf(2));
  });

  it.each([
    [
      ['body', 'feedback'],
      'too_long',
      'The feedback can have at most 4000 characters.',
    ],
    [
      ['body', 'scores', 0, 'comment'],
      'too_long',
      'The comment can have at most 1000 characters.',
    ],
    [
      ['body', 'evaluated_on'],
      'evaluated_on_in_future',
      "The date can't be after today.",
    ],
    [['body', 'evaluated_on'], 'evaluated_on_invalid', 'Choose a valid date.'],
  ])('422 %j %s lands on its field', async (path, code, message) => {
    await refusedSave(() =>
      problemWith(422, 'validation_error', { errors: [{ path, code }] }),
    );
    await settleUntil(() => text().includes(message));
  });

  it.each([
    'course_assessment_unknown',
    'course_assessment_requires_course_session',
    'criterion_unknown',
    'duplicate_criterion',
  ])('422 %s: the stale page message and Reload', async (code) => {
    await refusedSave(() =>
      problemWith(422, 'validation_error', {
        errors: [{ path: ['body', 'scores', 0, 'position'], code }],
      }),
    );
    await settleUntil(() => alerts(main()).length > 0);
    expect(buttonNamed(main(), 'Reload')).toBeTruthy();
  });

  it.each([
    ['evaluation_final', "This evaluation is final: it can't be deleted."],
    ['evaluation_not_draft', 'This evaluation is already final.'],
    ['evaluation_not_final', 'Only a final evaluation can be voided.'],
    ['evaluation_voided', 'This evaluation was voided.'],
  ])('409 %s: its message and Reload', async (code, message) => {
    await refusedSave(() => problemWith(409, code));
    await settleUntil(() => alerts(main()).some((a) => a.includes(message)));
    expect(buttonNamed(main(), 'Reload')).toBeTruthy();
    await settleUntil(() => focused() === failureRegion());
  });

  it.each([
    [
      'evaluation_author_only',
      'Only the tutor who wrote this evaluation can do this.',
    ],
    ['evaluation_read_only', 'This student is no longer assigned to you'],
    [
      'evaluation_tutor_only',
      'Only a tutor who currently teaches this student',
    ],
    ['permission_denied', "You don't have permission to do this."],
  ])(
    '403 %s: its message; Reload turns the page read-only',
    async (code, message) => {
      await refusedSave(() => problemWith(403, code));
      await settleUntil(() => alerts(main()).some((a) => a.includes(message)));
      api.set({ [DETAIL]: () => jsonResponse(200, FORMER_AUTHOR) });
      await click(buttonNamed(main(), 'Reload'));
      await settleUntil(() => main().querySelector('form') === null);
    },
  );

  it('404 shows the not-found page', async () => {
    await refusedSave(() => problemWith(404, 'not_found'));
    await settleUntil(
      () => main().querySelector('h1')?.textContent === 'Page not found',
    );
  });

  it('a 404 on the evaluation shows the not-found page', async () => {
    stub({ [DETAIL]: () => problemWith(404, 'not_found') });
    await open(
      PAGE,
      () => main().querySelector('h1')?.textContent === 'Page not found',
    );
  });
});

describe('My schedule links (D1)', () => {
  const NOW = Date.parse('2026-11-09T16:30:00Z');

  async function dialogFor(
    state: string,
    startsAt: string,
    audience: 'tutor' | 'family' = 'tutor',
  ) {
    stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const item = {
      ...tutorItem(),
      state,
      starts_at: startsAt,
      participants: [
        { student_id: STUDENT_ID, display_name: 'José Ñúñez Pérez' },
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

  const named = (links: HTMLAnchorElement[], name: string) =>
    links.find((a) => a.getAttribute('aria-label') === name);

  it.each([
    ['confirmed', '2026-11-09T16:00:00Z', true, true],
    ['completed', '2026-11-09T16:00:00Z', true, true],
    ['confirmed', '2026-11-09T17:00:00Z', true, false],
    ['offered', '2026-11-09T16:00:00Z', false, false],
    ['cancelled', '2026-11-09T16:00:00Z', false, false],
  ])(
    'a tutor %s entry starting %s: history %s, evaluate %s',
    async (state, startsAt, history, evaluate) => {
      const links = await dialogFor(state, startsAt);
      const historyLink = named(links, 'Academic history of José Ñúñez Pérez');
      const evaluateLink = named(links, 'Evaluate José Ñúñez Pérez');
      expect(Boolean(historyLink)).toBe(history);
      expect(Boolean(evaluateLink)).toBe(evaluate);
      if (historyLink) {
        expect(historyLink.textContent).toBe('Academic history');
        expect(historyLink.getAttribute('href')).toBe(
          `/students/${STUDENT_ID}/history`,
        );
      }
      if (evaluateLink) {
        expect(evaluateLink.textContent).toBe('Evaluate');
        expect(evaluateLink.getAttribute('href')).toBe(
          `/students/${STUDENT_ID}/evaluations/new?sessionId=${tutorItem().id}`,
        );
      }
    },
  );

  it('shows nothing to a family', async () => {
    const links = await dialogFor(
      'confirmed',
      '2026-11-09T16:00:00Z',
      'family',
    );
    expect(
      named(links, 'Academic history of José Ñúñez Pérez'),
    ).toBeUndefined();
    expect(named(links, 'Evaluate José Ñúñez Pérez')).toBeUndefined();
  });

  it('the rule is pure', () => {
    expect(
      tutorAcademicLinks(
        { state: 'confirmed', starts_at: '2026-11-09T16:30:00Z' },
        NOW,
      ),
    ).toEqual({ history: true, evaluate: true });
    expect(
      tutorAcademicLinks(
        { state: 'skipped_closure', starts_at: '2026-11-09T16:00:00Z' },
        NOW,
      ),
    ).toEqual({ history: false, evaluate: false });
  });
});
