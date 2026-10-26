import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
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
import type { AssignmentView } from './api';
import {
  ANA_ID,
  ASSIGNMENT,
  ASSIGNMENT_ID,
  FRESH,
  GUARDIAN,
  MARTA_ID,
  MATHS,
  PICKER_PAGE,
  REVIEWER,
  SESSION,
  SESSION_ID,
  STUDENT_ID,
  STUDENT_NAME,
  SUBJECTS_PAGE,
  TUTOR,
} from './fixtures';

// #313 B, A1, G1 (form): the class context (and its 404 / offered
// message), the fields' checks with focus on the first invalid one, the
// recipient picker (search sends q, select all / clear, 0 and 101 refused,
// a class's participants only, "Already handed in" locked on an edit), the
// max score locked once graded, clearing sends null (AM1), the POST and
// PATCH bodies and the navigation (the notice focused), every B6 row with
// its focus, Reload after a conflict (asks first; Escape/Cancel back to
// Reload; then the heading), the edit guard and leaving.

const CREATE = 'POST /api/v1/assignments';
const DETAIL = `GET /api/v1/assignments/${ASSIGNMENT_ID}`;
const PATCH = `PATCH /api/v1/assignments/${ASSIGNMENT_ID}`;
const SESSION_ROUTE = `GET /api/v1/class-sessions/${SESSION_ID}/attendance`;
const SUBJECTS = 'GET /api/v1/subjects';
const PICKER = 'GET /api/v1/students';
const NEW_PAGE = '/assignments/new';
const SESSION_PAGE = `/assignments/new?sessionId=${SESSION_ID}`;
const EDIT_PAGE = `/assignments/${ASSIGNMENT_ID}/edit`;
const ROSTER = `/assignments/${ASSIGNMENT_ID}`;
// 12:00 in Mexico City: today is 2030-11-09 (the fixtures' week).
const NOW = new Date('2030-11-09T18:00:00Z');
const TODAY = '2030-11-09';
const TOMORROW = '2030-11-10';

let app: RenderedApp | undefined;
let api: ApiStub;
let pickerUrls: URL[] = [];
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
const failure = () =>
  main().querySelector<HTMLElement>('[tabindex="-1"] [role="alert"]')
    ?.parentElement ?? null;
const describedText = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent)
    .join(' ');
/** The recipient checkbox (a Radix button) labelled `name`. */
function checkbox(name: string): HTMLButtonElement {
  const label = [...main().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === name,
  );
  const box = label ? document.getElementById(label.htmlFor) : null;
  if (!(box instanceof HTMLButtonElement)) {
    throw new Error(`No checkbox "${name}"`);
  }
  return box;
}
const isChecked = (name: string) =>
  checkbox(name).getAttribute('data-state') === 'checked';
const status = (wanted: string) =>
  [...main().querySelectorAll<HTMLElement>('[role="status"]')].find(
    (element) => element.textContent === wanted,
  );

function stub(routes: Routes = {}, as = TUTOR) {
  pickerUrls = [];
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [SESSION_ROUTE]: () => jsonResponse(200, SESSION),
    [SUBJECTS]: () => jsonResponse(200, SUBJECTS_PAGE),
    [PICKER]: (request) => {
      pickerUrls.push(new URL(request.url));
      return jsonResponse(200, PICKER_PAGE);
    },
    [DETAIL]: () => jsonResponse(200, ASSIGNMENT),
    ...routes,
  });
}

async function openNew(path = NEW_PAGE, routes: Routes = {}) {
  stub(routes);
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('form'));
}

async function openEdit(view: AssignmentView, routes: Routes = {}) {
  stub({ [DETAIL]: () => jsonResponse(200, view), ...routes });
  app = await renderApp(EDIT_PAGE);
  await settleUntil(() => main().querySelector('form'));
}

/** A valid create without a class (José checked). */
async function fillNew() {
  setFieldValue(field('Subject'), MATHS.id);
  setFieldValue(field('Title'), '  Fracciones  ');
  setFieldValue(field('Instructions'), 'Ejercicios 1 a 5.');
  setFieldValue(field('Due date'), TOMORROW);
  await settleUntil(() =>
    main().querySelector(`[data-student="${STUDENT_ID}"]`),
  );
  await click(checkbox(STUDENT_NAME));
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('access (A1)', () => {
  it.each([
    ['a Coordinator', REVIEWER, NEW_PAGE],
    ['a guardian', GUARDIAN, NEW_PAGE],
    ['a Coordinator', REVIEWER, EDIT_PAGE],
  ] as const)(
    '%s on %s gets access-denied, no request',
    async (_who, as, path) => {
      stub({}, as);
      app = await renderApp(path);
      await settleUntil(() => heading());
      expect(heading()!.textContent).toBe("You don't have access to this page");
      expect(
        api.requests.filter(
          (r) => r.path.includes('assignment') || r.path.includes('attendance'),
        ),
      ).toHaveLength(0);
    },
  );
});

describe('from a class (B1)', () => {
  it('fixes the subject, checks the participants, offers no search', async () => {
    await openNew(SESSION_PAGE);
    expect(heading()!.textContent).toBe('Set homework');
    expect(text()).toContain('For the class on Nov 10, 2030, 4:00 PM');
    expect(text()).toContain('Matemáticas (Secundaria)');
    expect(main().querySelector('select')).toBeNull();
    expect(isChecked(STUDENT_NAME)).toBe(true);
    expect(isChecked('Ana García')).toBe(true);
    expect(text()).toContain('2 selected');
    expect(() => field('Search students')).toThrow();
    expect(api.calls(PICKER)).toHaveLength(0);
  });

  it('sends the class and every checked participant explicitly', async () => {
    await openNew(SESSION_PAGE, {
      [CREATE]: () => jsonResponse(201, ASSIGNMENT),
    });
    await click(checkbox('Ana García'));
    setFieldValue(field('Title'), 'Tarea');
    setFieldValue(field('Instructions'), 'Hazla.');
    setFieldValue(field('Due date'), TOMORROW);
    await click(button('Save homework'));
    await settleUntil(() => api.calls(CREATE).length === 1);
    expect(api.calls(CREATE)[0]!.body).toEqual({
      subject_id: MATHS.id,
      title: 'Tarea',
      instructions: 'Hazla.',
      due_on: TOMORROW,
      class_session_id: SESSION_ID,
      student_ids: [STUDENT_ID],
    });
  });

  it.each([
    ['a 404', () => problemWith(404, 'not_found')],
    [
      'an offered class',
      () => jsonResponse(200, { ...SESSION, state: 'offered' }),
    ],
    [
      'a cancelled class',
      () => jsonResponse(200, { ...SESSION, state: 'cancelled' }),
    ],
  ] as const)(
    '%s: the message and the link without it, no form',
    async (_name, answer) => {
      stub({ [SESSION_ROUTE]: answer });
      app = await renderApp(SESSION_PAGE);
      await settleUntil(() =>
        text().includes("Homework can't be linked to this class."),
      );
      expect(main().querySelector('form')).toBeNull();
      const link = main().querySelector<HTMLAnchorElement>(
        `a[href="${NEW_PAGE}"]`,
      );
      expect(link?.textContent).toBe('Set homework without a class');
      expect(api.calls(CREATE)).toHaveLength(0);
    },
  );

  it('a sessionId that is not a UUID: the same message, nothing read', async () => {
    stub();
    app = await renderApp('/assignments/new?sessionId=nope');
    await settleUntil(() =>
      text().includes("Homework can't be linked to this class."),
    );
    expect(
      api.requests.filter((r) => r.path.includes('attendance')),
    ).toHaveLength(0);
  });
});

describe('fields (B2)', () => {
  it('labels, the date bounds and the help texts', async () => {
    await openNew();
    const options = [...field<HTMLSelectElement>('Subject').options].map(
      (o) => o.textContent,
    );
    expect(options).toEqual([
      'Choose a subject',
      'Matemáticas (Secundaria)',
      'Física (Secundaria)',
    ]);
    const due = field<HTMLInputElement>('Due date');
    expect(due.type).toBe('date');
    expect(due.min).toBe(TODAY);
    expect(due.max).toBe('2031-11-09');
    const time = field<HTMLInputElement>('Due time (optional)');
    expect(time.type).toBe('time');
    expect(describedText(time)).toBe(
      'Leave empty for the end of the day (center time).',
    );
    const max = field<HTMLInputElement>('Max score (optional)');
    expect(max.inputMode).toBe('decimal');
    expect(describedText(max)).toBe('Leave empty to give feedback only.');
    setFieldValue(field('Instructions'), '  Hola  ');
    expect(describedText(field('Instructions'))).toBe('4/4000 characters');
  });

  it('an empty form: field errors, focus on the first, nothing sent', async () => {
    await openNew();
    await click(button('Save homework'));
    await settleUntil(() => text().includes('Choose a subject.'));
    expect(text()).toContain('Write a title.');
    expect(text()).toContain('Write the instructions.');
    expect(text()).toContain('Choose the due date.');
    expect(text()).toContain('Choose at least one student.');
    const subject = field<HTMLSelectElement>('Subject');
    expect(subject.getAttribute('aria-invalid')).toBe('true');
    expect(describedText(subject)).toBe('Choose a subject.');
    await settleUntil(() => focused() === subject);
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('today with a time that passed: on Due time, focused', async () => {
    await openNew();
    await fillNew();
    setFieldValue(field('Due date'), TODAY);
    setFieldValue(field('Due time (optional)'), '11:59');
    await click(button('Save homework'));
    await settleUntil(() =>
      text().includes('That time has already passed today.'),
    );
    await settleUntil(() => focused() === field('Due time (optional)'));
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it.each([
    ['2030-11-08', "The due date can't be in the past."],
    ['2031-11-10', 'The due date can be at most a year from today.'],
  ])('due date %s: %s', async (date, message) => {
    await openNew();
    await fillNew();
    setFieldValue(field('Due date'), date);
    await click(button('Save homework'));
    await settleUntil(() => text().includes(message));
    await settleUntil(() => focused() === field('Due date'));
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it.each([
    ['7.555', 'Use at most two decimals.'],
    ['0.4', 'The max score is 0.5 to 1000.'],
    ['1000.01', 'The max score is 0.5 to 1000.'],
    ['7,5', 'Use a point for decimals, as in 7.5.'],
  ])('max score %s: %s (lib/decimal)', async (value, message) => {
    await openNew();
    await fillNew();
    setFieldValue(field('Max score (optional)'), value);
    await click(button('Save homework'));
    await settleUntil(() => text().includes(message));
    await settleUntil(() => focused() === field('Max score (optional)'));
    expect(api.calls(CREATE)).toHaveLength(0);
  });
});

describe('recipients (B3)', () => {
  it('lists the tutor’s students; search sends q after the debounce', async () => {
    await openNew();
    await settleUntil(() => pickerUrls.length === 1);
    expect(pickerUrls[0]!.searchParams.get('q')).toBeNull();
    expect(pickerUrls[0]!.searchParams.get('limit')).toBe('100');
    expect(checkbox('Marta Ruiz')).toBeTruthy();
    const fieldset = main().querySelector('fieldset')!;
    expect(fieldset.querySelector('legend')!.textContent).toBe('Students');
    setFieldValue(field('Search students'), 'Ana');
    await settleUntil(() => pickerUrls.length === 2, 3000);
    expect(pickerUrls[1]!.searchParams.get('q')).toBe('Ana');
  });

  it('Select all and Clear; the count is a polite live region', async () => {
    await openNew();
    await settleUntil(() => main().querySelector('[data-student]'));
    await click(button('Select all'));
    expect(
      isChecked(STUDENT_NAME) &&
        isChecked('Ana García') &&
        isChecked('Marta Ruiz'),
    ).toBe(true);
    const count = status('3 selected')!;
    expect(count.getAttribute('aria-live')).toBe('polite');
    await click(button('Clear'));
    expect(status('0 selected')).toBeTruthy();
  });

  it('a checked student stays listed while a search hides them', async () => {
    await openNew(NEW_PAGE, {
      [PICKER]: (request) => {
        const q = new URL(request.url).searchParams.get('q');
        return jsonResponse(
          200,
          q
            ? { ...PICKER_PAGE, items: [PICKER_PAGE.items[1]!], total: 1 }
            : PICKER_PAGE,
        );
      },
    });
    await settleUntil(() => main().querySelector('[data-student]'));
    await click(checkbox(STUDENT_NAME));
    setFieldValue(field('Search students'), 'Ana');
    await settleUntil(
      () => !main().querySelector(`[data-student="${MARTA_ID}"]`),
      3000,
    );
    expect(isChecked(STUDENT_NAME)).toBe(true);
    expect(checkbox('Ana García')).toBeTruthy();
  });

  it('more than 100 matches: a note to search; 101 checked is refused', async () => {
    const many = Array.from({ length: 101 }, (_, i) => ({
      ...PICKER_PAGE.items[0]!,
      id: `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
      given_names: `Alumno ${i}`,
    }));
    await openNew(NEW_PAGE, {
      [PICKER]: () =>
        jsonResponse(200, { ...PICKER_PAGE, items: many, total: 140 }),
    });
    await settleUntil(() => main().querySelector('[data-student]'));
    expect(text()).toContain(
      'Showing the first 101 students. Search to narrow the list.',
    );
    setFieldValue(field('Subject'), MATHS.id);
    setFieldValue(field('Title'), 'T');
    setFieldValue(field('Instructions'), 'I');
    setFieldValue(field('Due date'), TOMORROW);
    await click(button('Select all'));
    await click(button('Save homework'));
    await settleUntil(() => text().includes('Choose at most 100 students.'));
    const first = main().querySelector<HTMLElement>('[data-student]')!;
    await settleUntil(() => focused() === first);
    expect(api.calls(CREATE)).toHaveLength(0);
  });
});

describe('save (B5)', () => {
  it('POSTs once and goes to the roster, the notice focused', async () => {
    const created = { ...FRESH, counts: { ...FRESH.counts, recipients: 1 } };
    await openNew(NEW_PAGE, {
      [CREATE]: () => jsonResponse(201, created),
      [DETAIL]: () => jsonResponse(200, created),
    });
    await fillNew();
    setFieldValue(field('Due time (optional)'), '18:00');
    setFieldValue(field('Max score (optional)'), '10');
    await click(button('Save homework'));
    await settleUntil(() => app!.location() === ROSTER);
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(api.calls(CREATE)[0]!.body).toEqual({
      subject_id: MATHS.id,
      title: 'Fracciones',
      instructions: 'Ejercicios 1 a 5.',
      due_on: TOMORROW,
      due_time: '18:00',
      max_score: '10.00',
      student_ids: [STUDENT_ID],
    });
    const notice = await vi.waitFor(() => {
      const found = status('Homework set. 1 student was notified.');
      expect(found).toBeTruthy();
      return found!;
    });
    // #308 QA D1: focus lands on the notice, never on <body>.
    await settleUntil(() => focused() === notice);
  });

  it('the plural notice', async () => {
    await openNew(NEW_PAGE, {
      [CREATE]: () => jsonResponse(201, ASSIGNMENT),
    });
    await fillNew();
    await click(button('Save homework'));
    await settleUntil(() => status('Homework set. 5 students were notified.'));
  });
});

describe('edit (B4)', () => {
  it('max score locked once graded; handed-in students locked', async () => {
    await openEdit(ASSIGNMENT);
    expect(heading()!.textContent).toBe('Edit homework');
    expect(text()).toContain("The subject can't change.");
    const max = field<HTMLInputElement>('Max score (optional)');
    expect(max.disabled).toBe(true);
    expect(describedText(max)).toBe(
      "Grades have been given; the max score can't change.",
    );
    for (const name of ['Ana García', 'Luis Ortega', 'Pedro Gómez']) {
      const box = checkbox(name);
      expect(box.disabled).toBe(true);
      expect(isChecked(name)).toBe(true);
      expect(describedText(box)).toBe('Already handed in');
    }
    expect(checkbox(STUDENT_NAME).disabled).toBe(false);
    expect(text()).toContain('Added students are notified.');
  });

  it('a class’s homework offers its participants (no search)', async () => {
    await openEdit(ASSIGNMENT);
    expect(() => field('Search students')).toThrow();
    expect(api.calls(SESSION_ROUTE)).toHaveLength(1);
  });

  it('clearing the time and the max score sends null; the notice focused', async () => {
    await openEdit(FRESH, {
      [PATCH]: () => jsonResponse(200, { ...FRESH, version: 4 }),
    });
    setFieldValue(field('Due time (optional)'), '17:00');
    setFieldValue(field('Max score (optional)'), '');
    await click(checkbox('Ana García'));
    await click(button('Save homework'));
    await settleUntil(() => api.calls(PATCH).length === 1);
    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 3,
      due_time: '17:00',
      max_score: null,
      remove_student_ids: [ANA_ID],
    });
    await settleUntil(() => app!.location() === ROSTER);
    const notice = await vi.waitFor(() => {
      const found = status('Homework saved.');
      expect(found).toBeTruthy();
      return found!;
    });
    await settleUntil(() => focused() === notice);
  });

  it('clearing the time sends due_time: null (AM1)', async () => {
    await openEdit(ASSIGNMENT, {
      [PATCH]: () => jsonResponse(200, ASSIGNMENT),
    });
    expect(field<HTMLInputElement>('Due time (optional)').value).toBe('16:00');
    setFieldValue(field('Due time (optional)'), '');
    await click(button('Save homework'));
    await settleUntil(() => api.calls(PATCH).length === 1);
    expect(api.calls(PATCH)[0]!.body).toEqual({ version: 3, due_time: null });
  });

  it('without can_edit: the message, the link, no form', async () => {
    stub({
      [DETAIL]: () => jsonResponse(200, { ...ASSIGNMENT, can_edit: false }),
    });
    app = await renderApp(EDIT_PAGE);
    await settleUntil(() => text().includes("This homework can't be edited."));
    expect(main().querySelector('form')).toBeNull();
    expect(main().querySelector(`a[href="${ROSTER}"]`)?.textContent).toBe(
      'Back to the homework',
    );
  });
});

describe('refusals (B6)', () => {
  async function refusedCreate(answer: () => Response) {
    await openNew(NEW_PAGE, { [CREATE]: answer });
    await fillNew();
    await click(checkbox('Ana García'));
    await click(button('Save homework'));
    await settleUntil(() => api.calls(CREATE).length === 1);
  }

  it('422 recipient_invalid: on each named checkbox, a summary, focus on the first', async () => {
    await refusedCreate(() =>
      problemWith(422, 'validation_error', {
        errors: [{ path: ['body', 'student_ids'], code: 'recipient_invalid' }],
        details: { student_ids: [ANA_ID] },
      }),
    );
    await settleUntil(() =>
      text().includes("Some students can't receive this homework."),
    );
    const ana = checkbox('Ana García');
    expect(ana.getAttribute('aria-invalid')).toBe('true');
    expect(describedText(ana)).toBe('You no longer teach this student.');
    expect(checkbox(STUDENT_NAME).getAttribute('aria-invalid')).toBeNull();
    await settleUntil(() => focused() === ana);
    expect(field<HTMLInputElement>('Title').value).toBe('  Fracciones  ');
  });

  it.each([
    [
      'recipients_required',
      ['body', 'student_ids'],
      'Choose at least one student.',
    ],
    [
      'duplicate_recipient',
      ['body', 'student_ids'],
      'A student appears more than once.',
    ],
    [
      'recipients_too_many',
      ['body', 'student_ids'],
      'Choose at most 100 students.',
    ],
  ] as const)('422 %s: on the fieldset', async (code, path, message) => {
    await refusedCreate(() =>
      problemWith(422, 'validation_error', {
        errors: [{ path: [...path], code }],
      }),
    );
    await settleUntil(() => text().includes(message));
    const first = main().querySelector<HTMLElement>('[data-student]')!;
    await settleUntil(() => focused() === first);
  });

  it.each([
    [
      'due_in_past',
      ['body', 'due_on'],
      "The due date can't be in the past.",
      'Due date',
    ],
    [
      'due_too_far',
      ['body', 'due_on'],
      'The due date can be at most a year from today.',
      'Due date',
    ],
    [
      'score_precision',
      ['body', 'max_score'],
      'Use at most two decimals.',
      'Max score (optional)',
    ],
    [
      'max_score_out_of_range',
      ['body', 'max_score'],
      'The max score is 0.5 to 1000.',
      'Max score (optional)',
    ],
    [
      'too_long',
      ['body', 'title'],
      'The title can have at most 200 characters.',
      'Title',
    ],
    [
      'subject_unknown',
      ['body', 'subject_id'],
      'This subject is no longer available. Choose another one.',
      'Subject',
    ],
  ] as const)(
    '422 %s on %j: on its field, focused',
    async (code, path, message, label) => {
      await refusedCreate(() =>
        problemWith(422, 'validation_error', {
          errors: [{ path: [...path], code }],
        }),
      );
      await settleUntil(() => text().includes(message));
      const target = field(label);
      expect(target.getAttribute('aria-invalid')).toBe('true');
      await settleUntil(() => focused() === target);
    },
  );

  it('422 due_in_past for today with a time: on Due time', async () => {
    vi.setSystemTime(new Date('2030-11-09T14:00:00Z'));
    await openNew(NEW_PAGE, {
      [CREATE]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'due_on'], code: 'due_in_past' }],
        }),
    });
    await fillNew();
    setFieldValue(field('Due date'), TODAY);
    setFieldValue(field('Due time (optional)'), '09:00');
    await click(button('Save homework'));
    await settleUntil(() =>
      text().includes('That time has already passed today.'),
    );
    await settleUntil(() => focused() === field('Due time (optional)'));
  });

  it.each([
    ['class_session_invalid', 422],
    ['subject_mismatch', 422],
    ['session_not_assignable', 409],
  ] as const)(
    '%s: the class message and the link, focused',
    async (code, http) => {
      stub({
        [CREATE]: () =>
          http === 422
            ? problemWith(422, 'validation_error', {
                errors: [{ path: ['body', 'class_session_id'], code }],
              })
            : problemWith(409, code),
      });
      app = await renderApp(SESSION_PAGE);
      await settleUntil(() => main().querySelector('form'));
      setFieldValue(field('Title'), 'T');
      setFieldValue(field('Instructions'), 'I');
      setFieldValue(field('Due date'), TOMORROW);
      await click(button('Save homework'));
      await settleUntil(() =>
        text().includes("Homework can't be linked to this class."),
      );
      expect(main().querySelector(`a[href="${NEW_PAGE}"]`)?.textContent).toBe(
        'Set homework without a class',
      );
      await settleUntil(() => focused() === failure());
    },
  );

  it.each([
    ['assignment_tutor_only', 403, 'Only tutors set homework.'],
    [
      'assignment_creator_only',
      403,
      'Only the tutor who set this homework can change it.',
    ],
    [
      'assignment_read_only',
      403,
      'You no longer teach this student, so this homework is read-only.',
    ],
  ] as const)(
    '%s: its message and Reload, focused',
    async (code, http, message) => {
      await openEdit(FRESH, { [PATCH]: () => problemWith(http, code) });
      setFieldValue(field('Title'), 'Tecleado');
      await click(button('Save homework'));
      await settleUntil(() => text().includes(message));
      expect(button('Reload')).toBeTruthy();
      await settleUntil(() => focused() === failure());
      expect(field<HTMLInputElement>('Title').value).toBe('Tecleado');
    },
  );

  it('409 assignment_has_grades: on Max score and Reload', async () => {
    await openEdit(FRESH, {
      [PATCH]: () => problemWith(409, 'assignment_has_grades'),
    });
    setFieldValue(field('Max score (optional)'), '20');
    await click(button('Save homework'));
    await settleUntil(() => focused() === failure());
    const max = field('Max score (optional)');
    expect(describedText(max)).toContain(
      "Grades have been given; the max score can't change.",
    );
    expect(button('Reload')).toBeTruthy();
  });

  it('409 recipient_has_submission: "Already handed in" on the checkbox and Reload', async () => {
    await openEdit(FRESH, {
      [PATCH]: () =>
        problemWith(409, 'recipient_has_submission', {
          details: { student_ids: [ANA_ID] },
        }),
    });
    await click(checkbox('Ana García'));
    await click(button('Save homework'));
    await settleUntil(() => focused() === failure());
    expect(describedText(checkbox('Ana García'))).toBe('Already handed in');
    expect(text()).toContain(
      'A student you removed has already handed in work.',
    );
  });

  it('404 shows the not-found page', async () => {
    await openEdit(FRESH, { [PATCH]: () => problemWith(404, 'not_found') });
    setFieldValue(field('Title'), 'Tecleado');
    await click(button('Save homework'));
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });

  describe('409 version_conflict and Reload', () => {
    async function conflict() {
      await openEdit(FRESH, {
        [PATCH]: () => problemWith(409, 'version_conflict'),
        [DETAIL]: [
          () => jsonResponse(200, FRESH),
          () =>
            jsonResponse(200, {
              ...FRESH,
              version: 7,
              title: 'Cambiada en otra pestaña',
            }),
        ],
      });
      setFieldValue(field('Title'), 'Tecleado');
      await click(button('Save homework'));
      await settleUntil(() =>
        text().includes('This homework changed elsewhere.'),
      );
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
          field<HTMLInputElement>('Title').value === 'Cambiada en otra pestaña',
      );
      // #308 QA D3/D4: focus after the form shows the new version.
      await settleUntil(() => focused() === heading());
      expect(text()).not.toContain('This homework changed elsewhere.');
      api.set({ [PATCH]: () => jsonResponse(200, { ...FRESH, version: 8 }) });
      setFieldValue(field('Title'), 'Otra vez');
      await click(button('Save homework'));
      await settleUntil(() => api.calls(PATCH).length === 2);
      expect((api.calls(PATCH)[1]!.body as { version: number }).version).toBe(
        7,
      );
    });
  });
});

describe('leaving (B5)', () => {
  it('with unsaved changes asks first; Stay refocuses the link', async () => {
    await openEdit(FRESH);
    setFieldValue(field('Title'), 'Tecleado');
    const back = main().querySelector<HTMLAnchorElement>(
      `a[href="${ROSTER}"]`,
    )!;
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
