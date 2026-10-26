import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { FAMILY_STUDENTS } from '@/features/academics/history/fixtures';
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
import type { AssignmentView } from './api';
import {
  ANA_ID,
  ASSIGNMENT,
  ASSIGNMENT_ID,
  FEEDBACK_ONLY,
  FINANCE,
  FRESH,
  GRADED_ROW,
  GUARDIAN,
  LATE_ROW,
  LUIS_ID,
  OTHER_ASSIGNMENT_ID,
  READ_ONLY,
  REVIEWER,
  SCRIPT,
  SCRIPTED,
  SECOND_PAGE,
  studentItem,
  SUBJECTS_PAGE,
  TUTOR,
  WORKING_LIST,
} from './fixtures';

// #313 C, A1, G1 (list and roster): the working list (filters in the URL,
// the count texts and plurals, "Load more", "Set homework" for tutors
// only, the empty states, Clear filters focusing the heading), the roster
// (badges as text, "Late", "Recorded by the tutor", the internal reason,
// links by the C6 rule, markup as text), actions strictly by the flags
// (creator, holder, former creator), the record dialog, the grade dialog
// (scored and feedback-only, first grade vs re-grade, the cache, every C5
// error), the delete dialog, every focus move, and the route access.

const LIST = 'GET /api/v1/assignments';
const DETAIL = `GET /api/v1/assignments/${ASSIGNMENT_ID}`;
const DELETE = `DELETE /api/v1/assignments/${ASSIGNMENT_ID}`;
const SUBJECTS = 'GET /api/v1/subjects';
const grade = (studentId: string) =>
  `PUT /api/v1/assignments/${ASSIGNMENT_ID}/students/${studentId}/grade`;
const record = (studentId: string) =>
  `PUT /api/v1/assignments/${ASSIGNMENT_ID}/students/${studentId}/submission`;
const PAGE = `/assignments/${ASSIGNMENT_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
let listUrls: URL[] = [];
let deleteUrls: URL[] = [];
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const focused = () => document.activeElement;
const heading = () => main().querySelector('h1');
const button = (name: string) => buttonNamed(main(), name);
const buttons = () =>
  [...main().querySelectorAll('button')].map((b) => b.textContent);
const status = (wanted: string) =>
  [...main().querySelectorAll<HTMLElement>('[role="status"]')].find(
    (element) => element.textContent === wanted,
  );
const card = (name: string) =>
  [...main().querySelectorAll('li')].find(
    (li) => li.querySelector('h3')?.textContent === name,
  )!;
const dialogField = <T extends HTMLInputElement | HTMLTextAreaElement>(
  label: string,
) => labelled<T>(openDialog()!, label);

function stub(routes: Routes = {}, as = TUTOR) {
  listUrls = [];
  deleteUrls = [];
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [LIST]: (request) => {
      const url = new URL(request.url);
      listUrls.push(url);
      return jsonResponse(
        200,
        url.searchParams.get('cursor') ? SECOND_PAGE : WORKING_LIST,
      );
    },
    [SUBJECTS]: () => jsonResponse(200, SUBJECTS_PAGE),
    [DETAIL]: () => jsonResponse(200, ASSIGNMENT),
    ...routes,
  });
}

async function openRoster(
  view: AssignmentView = ASSIGNMENT,
  as = TUTOR,
  routes: Routes = {},
) {
  stub({ [DETAIL]: () => jsonResponse(200, view), ...routes }, as);
  app = await renderApp(PAGE);
  await settleUntil(() => heading()?.textContent === view.title);
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
  it.each(['/assignments', PAGE])(
    'Finance gets access-denied on %s, no request',
    async (path) => {
      stub({}, FINANCE);
      app = await renderApp(path);
      await settleUntil(() => heading());
      expect(heading()!.textContent).toBe("You don't have access to this page");
      expect(
        api.requests.filter((r) => r.path.includes('assignments')),
      ).toHaveLength(0);
    },
  );

  it.each([
    ['/assignments', '/my-homework'],
    [PAGE, `/my-homework/${ASSIGNMENT_ID}`],
  ])('a guardian on %s goes to %s, no working request', async (path, to) => {
    stub(
      {
        'GET /api/v1/schedule/family-students': () =>
          jsonResponse(200, FAMILY_STUDENTS),
        [`GET /api/v1/students/${FAMILY_STUDENTS.items[0]!.student_id}/assignments`]:
          () =>
            jsonResponse(200, {
              items: [],
              summary: { pending: 0, missing: 0, submitted: 0, graded: 0 },
              next_cursor: null,
            }),
        [`GET /api/v1/students/${FAMILY_STUDENTS.items[0]!.student_id}/assignments/${ASSIGNMENT_ID}`]:
          () => jsonResponse(200, studentItem({ can_submit: true })),
      },
      GUARDIAN,
    );
    app = await renderApp(path);
    await settleUntil(() => app!.location() === to);
    expect(api.calls(LIST)).toHaveLength(0);
    expect(api.calls(DETAIL)).toHaveLength(0);
  });
});

describe('the working list (C1)', () => {
  async function openList(
    path = '/assignments',
    as = TUTOR,
    routes: Routes = {},
  ) {
    stub(routes, as);
    app = await renderApp(path);
    await settleUntil(
      () =>
        main().querySelector('li') ||
        status('No homework yet.') ||
        status('No homework matches these filters.'),
    );
  }

  it('rows as cards with the deadline, the creator and the counts', async () => {
    await openList();
    expect(heading()!.textContent).toBe('Assigned homework');
    expect(listUrls[0]!.searchParams.get('due')).toBe('upcoming');
    expect(listUrls[0]!.searchParams.get('limit')).toBe('20');
    const links = [...main().querySelectorAll<HTMLAnchorElement>('li h2 a')];
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Fracciones equivalentes', PAGE],
      ['Lectura de física', `/assignments/${OTHER_ASSIGNMENT_ID}`],
    ]);
    const [own, other] = [...main().querySelectorAll('li')];
    expect(own!.textContent).toContain('Due Nov 12, 2030 at 4:00 PM');
    expect(own!.textContent).toContain(
      '2 handed in · 1 graded · 1 missing · 1 pending, of 5 students · 1 late',
    );
    expect(own!.textContent).not.toContain('Set by');
    expect(other!.textContent).toContain('Set by Pedro Gómez');
    expect(other!.textContent).toContain('Due Nov 12, 2030');
    expect(other!.textContent).toContain(
      '0 handed in · 0 graded · 0 missing · 2 pending, of 2 students',
    );
    expect(other!.textContent).not.toContain('late');
  });

  it('"Set homework" for tutors only', async () => {
    await openList();
    expect(
      main().querySelector('a[href="/assignments/new"]')?.textContent,
    ).toBe('Set homework');
    app!.unmount();
    document.body.innerHTML = '';
    await openList('/assignments', REVIEWER);
    expect(main().querySelector('a[href="/assignments/new"]')).toBeNull();
  });

  it('filters go to the URL and the request', async () => {
    await openList();
    setFieldValue(labelled(main(), 'Due'), 'past');
    await settleUntil(() => listUrls.length === 2);
    expect(app!.location()).toBe('/assignments?due=past');
    expect(listUrls[1]!.searchParams.get('due')).toBe('past');
    setFieldValue(labelled(main(), 'Subject'), SUBJECTS_PAGE.items[1]!.id);
    await settleUntil(() => listUrls.length === 3);
    expect(listUrls[2]!.searchParams.get('subject_id')).toBe(
      SUBJECTS_PAGE.items[1]!.id,
    );
    setFieldValue(labelled(main(), 'Due'), 'all');
    await settleUntil(() => listUrls.length === 4);
    expect(listUrls[3]!.searchParams.get('due')).toBeNull();
  });

  it('Load more follows the cursor', async () => {
    await openList();
    await click(button('Load more'));
    await settleUntil(() => main().querySelectorAll('li').length === 3);
    expect(listUrls[1]!.searchParams.get('cursor')).toBe('cursor-2');
    expect(buttons()).not.toContain('Load more');
  });

  it('empty: the message and "Set homework"', async () => {
    await openList('/assignments', TUTOR, {
      [LIST]: () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    expect(status('No homework yet.')).toBeTruthy();
    expect(main().querySelectorAll('a[href="/assignments/new"]')).toHaveLength(
      1,
    );
  });

  it('empty with filters: Clear filters, then focus on the heading', async () => {
    await openList('/assignments?due=past', TUTOR, {
      [LIST]: () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    expect(status('No homework matches these filters.')).toBeTruthy();
    await click(button('Clear filters'));
    await settleUntil(() => app!.location() === '/assignments');
    await settleUntil(() => focused() === heading());
  });

  it('an error offers "Try again"', async () => {
    stub({ [LIST]: () => problemWith(500, 'internal_error') });
    app = await renderApp('/assignments');
    await settleUntil(() => main().querySelector('[role="alert"]'));
    expect(button('Try again')).toBeTruthy();
  });
});

describe('the roster (C2, C3, C6)', () => {
  it('shows the header and every row as text', async () => {
    await openRoster();
    expect(text()).toContain('Matemáticas');
    expect(text()).toContain('Due Nov 12, 2030 at 4:00 PM');
    expect(text()).toContain('Max score 10.00');
    expect(text()).toContain('For the class on Nov 10, 2030');
    expect(text()).toContain('Set by Carmen Ruiz');
    expect(text()).toContain('Resuelve los ejercicios 1 a 5.');
    expect(text()).toContain(
      '2 handed in · 1 graded · 1 missing · 1 pending, of 5 students · 1 late',
    );
    const names = [...main().querySelectorAll('li h3')].map(
      (h) => h.textContent,
    );
    expect(names).toEqual([
      'Ana García',
      'José Ñúñez Pérez',
      'Luis Ortega',
      'Marta Ruiz',
      'Pedro Gómez',
    ]);
    const ana = card('Ana García');
    expect(ana.textContent).toContain('Handed in');
    expect(ana.textContent).toContain('Late');
    expect(ana.textContent).toContain('Handed in on Nov 12, 2030, 7:00 PM');
    expect(ana.querySelector('.whitespace-pre-line')!.textContent).toBe(
      LATE_ROW.text,
    );
    // Badges: text plus a decorative icon, never colour only.
    for (const icon of ana.querySelectorAll('svg')) {
      expect(icon.getAttribute('aria-hidden')).toBe('true');
    }
    expect(card('José Ñúñez Pérez').textContent).toContain('Pending');
    expect(card('Marta Ruiz').textContent).toContain('Missing');
    expect(card('Pedro Gómez').textContent).toContain('Recorded by the tutor');
    const luis = card('Luis Ortega');
    expect(luis.textContent).toContain('Graded');
    expect(luis.textContent).toContain('8.00 of 10.00 (80.00 %)');
    expect(luis.textContent).toContain('Buen trabajo.');
    expect(luis.textContent).toContain('Graded on Nov 12, 2030');
    expect(luis.textContent).toContain('Updated');
    expect(luis.textContent).toContain(
      'Last change reason (internal): Revisé la pregunta 2.',
    );
  });

  it('a safe link opens in a new tab with noopener and nofollow', async () => {
    await openRoster();
    const anchor = card('Ana García').querySelector<HTMLAnchorElement>('a')!;
    expect(anchor.getAttribute('href')).toBe(LATE_ROW.url);
    expect(anchor.target).toBe('_blank');
    expect(anchor.rel).toBe('noopener noreferrer nofollow');
    expect(anchor.textContent).toBe(`${LATE_ROW.url} (opens in a new tab)`);
    expect(anchor.querySelector('.sr-only')!.textContent).toBe(
      ' (opens in a new tab)',
    );
  });

  it('markup is text; a stored javascript: link is never an anchor', async () => {
    await openRoster(SCRIPTED);
    expect(main().querySelector('script')).toBeNull();
    expect(text().split(SCRIPT).length - 1).toBeGreaterThanOrEqual(5);
    expect(main().querySelector('a[href^="javascript"]')).toBeNull();
    expect(card('Ana García').textContent).toContain('javascript:alert(1)');
  });

  it('feedback only: no score, "Feedback only"', async () => {
    await openRoster(FEEDBACK_ONLY);
    expect(text()).toContain('Feedback only');
    expect(text()).not.toContain('of 10.00');
    expect(card('Luis Ortega').textContent).toContain('Buen trabajo.');
  });

  it('a 404 shows the not-found page', async () => {
    stub({ [DETAIL]: () => problemWith(404, 'not_found') });
    app = await renderApp(PAGE);
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });
});

describe('actions by the flags (C2, C4)', () => {
  const actionButtons = () =>
    buttons().filter((b) =>
      ['Record handed-in work', 'Grade', 'Change grade', 'Delete'].includes(
        b ?? '',
      ),
    );

  it('the creator: Edit, Record for the unsubmitted, Grade / Change grade', async () => {
    await openRoster();
    expect(main().querySelector(`a[href="${PAGE}/edit"]`)?.textContent).toBe(
      'Edit',
    );
    expect(actionButtons()).toEqual([
      'Grade',
      'Record handed-in work',
      'Change grade',
      'Record handed-in work',
      'Grade',
    ]);
    expect(
      card('José Ñúñez Pérez')
        .querySelector('button')!
        .getAttribute('aria-label'),
    ).toBe('Record handed-in work: José Ñúñez Pérez');
    expect(
      card('Ana García').querySelector('button')!.getAttribute('aria-label'),
    ).toBe('Grade Ana García');
  });

  it.each([
    ['a holder', REVIEWER],
    ['a former creator', TUTOR],
  ] as const)('%s with false flags: no control at all', async (_who, as) => {
    await openRoster(READ_ONLY, as);
    expect(actionButtons()).toEqual([]);
    expect(main().querySelector(`a[href="${PAGE}/edit"]`)).toBeNull();
    // The internal reason stays visible to the roster audience.
    expect(text()).toContain('Last change reason (internal)');
  });

  it('per-row flags: Record without Grade, and no Grade where can_grade is false', async () => {
    await openRoster({
      ...ASSIGNMENT,
      roster: ASSIGNMENT.roster.map((row) =>
        row.student.id === ANA_ID ? { ...row, can_grade: false } : row,
      ),
    });
    expect(card('Ana García').querySelector('button')).toBeNull();
    expect(card('José Ñúñez Pérez').querySelector('button')!.textContent).toBe(
      'Record handed-in work',
    );
    expect(card('Luis Ortega').querySelector('button')!.textContent).toBe(
      'Change grade',
    );
  });

  it('Delete only with can_delete', async () => {
    await openRoster(FRESH);
    expect(actionButtons()).toContain('Delete');
  });
});

describe('record handed-in work (C4)', () => {
  it('Escape and Cancel return focus to the trigger', async () => {
    await openRoster();
    for (const how of ['Escape', 'Cancel']) {
      const trigger = card('Marta Ruiz').querySelector('button')!;
      trigger.focus();
      await click(trigger);
      expect(openDialog()!.textContent).toContain(
        'Use this for work handed in on paper or in class.',
      );
      if (how === 'Escape') {
        await pressEscape();
      } else {
        await click(buttonNamed(openDialog()!, 'Cancel'));
      }
      await settleUntil(() => openDialog() === null);
      await settleUntil(
        () => focused() === card('Marta Ruiz').querySelector('button'),
      );
    }
  });

  it('a bad link: on the field, focused, nothing sent', async () => {
    await openRoster();
    await click(card('Marta Ruiz').querySelector('button')!);
    setFieldValue(dialogField('Link (optional)'), 'javascript:alert(1)');
    await click(buttonNamed(openDialog()!, 'Record work'));
    const link = dialogField<HTMLInputElement>('Link (optional)');
    await settleUntil(() => link.getAttribute('aria-invalid') === 'true');
    await settleUntil(() => focused() === link);
    expect(api.requests.filter((r) => r.method === 'PUT')).toHaveLength(0);
  });

  it('sends the note without a version, reads the roster, focuses the notice', async () => {
    const MARTA = ASSIGNMENT.roster[3]!.student.id;
    await openRoster(ASSIGNMENT, TUTOR, {
      [record(MARTA)]: () => jsonResponse(200, studentItem()),
    });
    await click(card('Marta Ruiz').querySelector('button')!);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Record handed-in work: Marta Ruiz');
    expect(dialogField<HTMLInputElement>('Link (optional)').type).toBe('url');
    setFieldValue(dialogField('Note (optional)'), '  En papel  ');
    await click(buttonNamed(dialog, 'Record work'));
    await settleUntil(() => api.calls(record(MARTA)).length === 1);
    expect(api.calls(record(MARTA))[0]!.body).toEqual({
      text: 'En papel',
      url: null,
    });
    await settleUntil(() => api.calls(DETAIL).length === 2);
    const notice = await vi.waitFor(() => {
      const found = status('Handed-in work recorded.');
      expect(found).toBeTruthy();
      return found!;
    });
    await settleUntil(() => focused() === notice);
    expect(openDialog()).toBeNull();
  });
});

describe('the grade dialog (C5)', () => {
  it('a first grade: the score, no change_reason, the row replaced, focus back', async () => {
    const graded = {
      ...LATE_ROW,
      version: 3,
      state: 'graded' as const,
      score: '8.00',
      score_percent: '80.00',
      graded_at: '2030-11-14T18:00:00Z',
    };
    await openRoster(ASSIGNMENT, TUTOR, {
      [grade(ANA_ID)]: () => jsonResponse(200, graded),
      [DETAIL]: [
        () => jsonResponse(200, ASSIGNMENT),
        () =>
          jsonResponse(200, {
            ...ASSIGNMENT,
            roster: ASSIGNMENT.roster.map((row) =>
              row.student.id === ANA_ID ? graded : row,
            ),
          }),
      ],
    });
    await click(card('Ana García').querySelector('button')!);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Grade Ana García');
    expect(dialog.textContent).toContain(
      'Ana García and guardians with access see the grade right away.',
    );
    expect(dialog.textContent).toContain('of 10.00');
    expect(() => dialogField('Reason for the change')).toThrow();
    setFieldValue(dialogField('Score'), '8');
    await click(buttonNamed(dialog, 'Save grade'));
    await settleUntil(() => api.calls(grade(ANA_ID)).length === 1);
    expect(api.calls(grade(ANA_ID))[0]!.body).toEqual({
      version: 2,
      score: '8.00',
      feedback: null,
    });
    await settleUntil(() =>
      card('Ana García').textContent?.includes('8.00 of 10.00 (80.00 %)'),
    );
    expect(status('Grade saved.')).toBeTruthy();
    // C5: focus returns to the row's button (now "Change grade").
    await settleUntil(
      () =>
        focused() ===
        card('Ana García').querySelector(`[data-grade="${ANA_ID}"]`),
    );
    expect((focused() as HTMLElement).textContent).toBe('Change grade');
  });

  it('a re-grade needs a reason; it is sent as change_reason', async () => {
    await openRoster(ASSIGNMENT, TUTOR, {
      [grade(LUIS_ID)]: () =>
        jsonResponse(200, { ...GRADED_ROW, version: 5, score: '9.00' }),
    });
    await click(
      card('Luis Ortega').querySelector(`[data-grade="${LUIS_ID}"]`)!,
    );
    const dialog = openDialog()!;
    expect(dialogField<HTMLInputElement>('Score').value).toBe('8.00');
    expect(dialog.textContent).toContain(
      "The reason stays internal: the student and guardians only see 'Updated'.",
    );
    setFieldValue(dialogField('Score'), '9');
    await click(buttonNamed(dialog, 'Save grade'));
    const reason = dialogField<HTMLTextAreaElement>('Reason for the change');
    await settleUntil(() => focused() === reason);
    expect(reason.getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(grade(LUIS_ID))).toHaveLength(0);
    setFieldValue(reason, '  Revisé otra vez  ');
    await click(buttonNamed(dialog, 'Save grade'));
    await settleUntil(() => api.calls(grade(LUIS_ID)).length === 1);
    expect(api.calls(grade(LUIS_ID))[0]!.body).toEqual({
      version: 4,
      score: '9.00',
      feedback: 'Buen trabajo.',
      change_reason: 'Revisé otra vez',
    });
  });

  it.each([
    ['10.01', 'The score is 0 to the max score.'],
    ['7.555', 'Use at most two decimals.'],
    ['', 'Give a score.'],
  ])('score %j: "%s", focused, nothing sent', async (score, message) => {
    await openRoster();
    await click(card('Ana García').querySelector('button')!);
    setFieldValue(dialogField('Score'), score);
    await click(buttonNamed(openDialog()!, 'Save grade'));
    await settleUntil(() => openDialog()!.textContent?.includes(message));
    await settleUntil(() => focused() === dialogField('Score'));
    expect(api.requests.filter((r) => r.method === 'PUT')).toHaveLength(0);
  });

  it('feedback only: no score field; feedback required', async () => {
    await openRoster(FEEDBACK_ONLY, TUTOR, {
      [grade(ANA_ID)]: () =>
        jsonResponse(200, { ...LATE_ROW, state: 'graded' }),
    });
    await click(card('Ana García').querySelector('button')!);
    expect(() => dialogField('Score')).toThrow();
    await click(buttonNamed(openDialog()!, 'Save grade'));
    const feedback = dialogField<HTMLTextAreaElement>('Feedback');
    await settleUntil(() => focused() === feedback);
    expect(openDialog()!.textContent).toContain('Write feedback.');
    setFieldValue(feedback, 'Bien hecho');
    await click(buttonNamed(openDialog()!, 'Save grade'));
    await settleUntil(() => api.calls(grade(ANA_ID)).length === 1);
    expect(api.calls(grade(ANA_ID))[0]!.body).toEqual({
      version: 2,
      feedback: 'Bien hecho',
    });
  });

  it('Escape and Cancel return focus to the trigger', async () => {
    await openRoster();
    for (const how of ['Escape', 'Cancel']) {
      const trigger = card('Ana García').querySelector<HTMLElement>(
        `[data-grade="${ANA_ID}"]`,
      )!;
      trigger.focus();
      await click(trigger);
      if (how === 'Escape') {
        await pressEscape();
      } else {
        await click(buttonNamed(openDialog()!, 'Cancel'));
      }
      await settleUntil(() => openDialog() === null);
      await settleUntil(() => focused() === trigger);
    }
  });

  it.each([
    [
      'score_out_of_range',
      ['body', 'score'],
      'Score',
      "The score must be between zero and the criterion's maximum.",
    ],
    ['score_precision', ['body', 'score'], 'Score', null],
    ['score_required', ['body', 'score'], 'Score', null],
    [
      'too_long',
      ['body', 'feedback'],
      'Feedback (optional)',
      'This text is too long.',
    ],
    ['change_reason_required', ['body', 'change_reason'], null, null],
  ] as const)(
    '422 %s: on its field, focused',
    async (code, path, label, message) => {
      const row = code === 'change_reason_required' ? LUIS_ID : ANA_ID;
      await openRoster(ASSIGNMENT, TUTOR, {
        [grade(row)]: () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: [...path], code }],
          }),
      });
      await click(
        card(row === ANA_ID ? 'Ana García' : 'Luis Ortega').querySelector(
          `[data-grade="${row}"]`,
        )!,
      );
      setFieldValue(dialogField('Score'), '8');
      if (row === LUIS_ID) {
        setFieldValue(dialogField('Reason for the change'), 'x');
      }
      await click(buttonNamed(openDialog()!, 'Save grade'));
      await settleUntil(() => api.calls(grade(row)).length === 1);
      const target = dialogField(label ?? 'Reason for the change');
      await settleUntil(() => target.getAttribute('aria-invalid') === 'true');
      await settleUntil(() => focused() === target);
      if (message) {
        expect(openDialog()!.textContent).toContain(message);
      }
    },
  );

  it.each([
    ['version_conflict', 'This homework changed elsewhere.'],
    [
      'submission_required',
      'Nothing has been handed in yet, so there is nothing to grade.',
    ],
  ])(
    '409 %s: the message and Reload; Reload refetches and focuses the heading',
    async (code, message) => {
      await openRoster(ASSIGNMENT, TUTOR, {
        [grade(ANA_ID)]: () => problemWith(409, code),
      });
      await click(card('Ana García').querySelector('button')!);
      setFieldValue(dialogField('Score'), '8');
      await click(buttonNamed(openDialog()!, 'Save grade'));
      await settleUntil(() => openDialog()!.textContent?.includes(message));
      const region =
        openDialog()!.querySelector<HTMLElement>('[tabindex="-1"]')!;
      await settleUntil(() => focused() === region);
      await click(buttonNamed(openDialog()!, 'Reload'));
      await settleUntil(() => openDialog() === null);
      await settleUntil(() => api.calls(DETAIL).length === 2);
      await settleUntil(() => focused() === heading());
    },
  );

  it('403 assignment_read_only: the message, no Reload', async () => {
    await openRoster(ASSIGNMENT, TUTOR, {
      [grade(ANA_ID)]: () => problemWith(403, 'assignment_read_only'),
    });
    await click(card('Ana García').querySelector('button')!);
    setFieldValue(dialogField('Score'), '8');
    await click(buttonNamed(openDialog()!, 'Save grade'));
    await settleUntil(() =>
      openDialog()!.textContent?.includes(
        'You no longer teach this student, so this homework is read-only.',
      ),
    );
    expect(() => buttonNamed(openDialog()!, 'Reload')).toThrow();
  });
});

describe('delete (C2)', () => {
  it('Escape returns focus to Delete', async () => {
    await openRoster(FRESH);
    const trigger = button('Delete');
    trigger.focus();
    await click(trigger);
    expect(openDialog()!.textContent).toContain(
      'Students and guardians will no longer see it.',
    );
    await pressEscape();
    await settleUntil(() => openDialog() === null);
    await settleUntil(() => focused() === button('Delete'));
  });

  it('confirm: DELETE with the version, back to the list, the notice focused', async () => {
    await openRoster(FRESH, TUTOR, {
      [DELETE]: (request) => {
        deleteUrls.push(new URL(request.url));
        return noContent();
      },
    });
    await click(button('Delete'));
    await click(buttonNamed(openDialog()!, 'Delete homework'));
    await settleUntil(() => app!.location() === '/assignments');
    expect(deleteUrls[0]!.searchParams.get('version')).toBe('3');
    const notice = await vi.waitFor(() => {
      const found = status('Homework deleted.');
      expect(found).toBeTruthy();
      return found!;
    });
    await settleUntil(() => focused() === notice);
  });

  it('409 assignment_has_submissions: the message and Reload, focused', async () => {
    await openRoster(FRESH, TUTOR, {
      [DELETE]: () => problemWith(409, 'assignment_has_submissions'),
    });
    await click(button('Delete'));
    await click(buttonNamed(openDialog()!, 'Delete homework'));
    await settleUntil(() => openDialog() === null);
    await settleUntil(() =>
      text().includes(
        "Students have handed in work, so this homework can't be deleted.",
      ),
    );
    const region = button('Reload').closest<HTMLElement>('[tabindex="-1"]')!;
    await settleUntil(() => focused() === region);
  });
});
