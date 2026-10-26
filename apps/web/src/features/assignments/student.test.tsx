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
import { click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { StudentAssignment } from './api';
import {
  ASSIGNMENT_ID,
  CLOSED_ITEM,
  FEEDBACK_ITEM,
  FINANCE,
  GRADED_ITEM,
  GUARDIAN,
  LONG_URL,
  MISSING_ITEM,
  PENDING_ITEM,
  SCRIPT,
  STUDENT,
  STUDENT_ID,
  STUDENT_LIST,
  studentItem,
  SUBMITTED_ITEM,
  submission,
  TUTOR,
} from './fixtures';

// #313 E, A1, A2, G1 (students and families): the picker (default,
// ?student=, a foreign value falls back with no request for it, a single
// entry as text, none), the summary buttons ↔ ?state=, the rows, the
// states (consent from the picker and from a 403, the inline 404s, empty,
// empty with a filter and "Show all", error), and the detail: the hand-in
// form (one of text or link, the link rule, first vs replacement body with
// the version, prefill, help texts, every E6 refusal with its focus,
// Reload), the read-only states, guardians never see the form, plain text
// and links by the C6 rule.

const FAMILY = 'GET /api/v1/schedule/family-students';
const listRoute = (id: string) => `GET /api/v1/students/${id}/assignments`;
const itemRoute = (id: string, aid = ASSIGNMENT_ID) =>
  `GET /api/v1/students/${id}/assignments/${aid}`;
const submitRoute = (id: string, aid = ASSIGNMENT_ID) =>
  `PUT /api/v1/assignments/${aid}/students/${id}/submission`;
const FOREIGN = '00000000-0000-4000-8000-000000999999';
const SELF_ITEM = `/my-homework/${ASSIGNMENT_ID}?student=${STUDENT_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
let listUrls: URL[] = [];
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const focused = () => document.activeElement;
const heading = () => main().querySelector('h1');
const button = (name: string) => buttonNamed(main(), name);
const field = <T extends HTMLInputElement | HTMLTextAreaElement>(
  label: string,
) => labelled<T>(main(), label);
const status = (wanted: string) =>
  [...main().querySelectorAll<HTMLElement>('[role="status"]')].find(
    (element) => element.textContent === wanted,
  );
const listCalls = () =>
  api.requests.filter((r) => r.path.endsWith('/assignments'));
const failure = () =>
  main().querySelector<HTMLElement>('[tabindex="-1"] [role="alert"]')
    ?.parentElement ?? null;
const describedText = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent)
    .join(' ');

function stub(routes: Routes = {}, as = GUARDIAN) {
  listUrls = [];
  const list = (request: Request) => {
    listUrls.push(new URL(request.url));
    return jsonResponse(200, STUDENT_LIST);
  };
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [FAMILY]: () =>
      jsonResponse(200, as === STUDENT ? SELF_ONLY : FAMILY_STUDENTS),
    [listRoute(CHILD_ID)]: list,
    [listRoute(SIBLING_ID)]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, {
        items: [],
        summary: { pending: 0, missing: 0, submitted: 0, graded: 0 },
        next_cursor: null,
      });
    },
    [listRoute(STUDENT_ID)]: list,
    [itemRoute(CHILD_ID)]: () =>
      jsonResponse(200, { ...PENDING_ITEM, can_submit: true }),
    [itemRoute(STUDENT_ID)]: () => jsonResponse(200, PENDING_ITEM),
    ...routes,
  });
}

async function open(path: string, routes: Routes = {}, as = GUARDIAN) {
  stub(routes, as);
  app = await renderApp(path);
  await settleUntil(
    () =>
      main().querySelector('li') ||
      main().querySelector('form') ||
      main().querySelector('[role="status"]') ||
      main().querySelector('section h2') ||
      main().querySelector('[role="alert"]'),
  );
}

async function openSelf(item: StudentAssignment, routes: Routes = {}) {
  await open(
    SELF_ITEM,
    { [itemRoute(STUDENT_ID)]: () => jsonResponse(200, item), ...routes },
    STUDENT,
  );
  await settleUntil(() => heading()?.textContent === item.title);
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
  it.each([
    ['a tutor-only user', TUTOR],
    ['Finance', FINANCE],
  ] as const)(
    '%s gets access-denied on /my-homework, no request',
    async (_who, as) => {
      stub({}, as);
      app = await renderApp('/my-homework');
      await settleUntil(() => heading());
      expect(heading()!.textContent).toBe("You don't have access to this page");
      expect(api.calls(FAMILY)).toHaveLength(0);
      expect(listCalls()).toHaveLength(0);
    },
  );
});

describe('the picker (E1)', () => {
  it('a guardian: the first child by default, "Whose homework"', async () => {
    await open('/my-homework');
    expect(heading()!.textContent).toBe('Homework');
    const select = labelled<HTMLSelectElement>(main(), 'Whose homework');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'Lucía Pérez',
      'Andrés Pérez',
      'Mateo Pérez (not shared)',
    ]);
    expect(select.value).toBe(CHILD_ID);
    expect(listCalls().map((r) => r.path)).toEqual([
      `/api/v1/students/${CHILD_ID}/assignments`,
    ]);
  });

  it('a choice goes to ?student=', async () => {
    await open('/my-homework');
    setFieldValue(labelled(main(), 'Whose homework'), SIBLING_ID);
    await settleUntil(
      () => app!.location() === `/my-homework?student=${SIBLING_ID}`,
    );
    await settleUntil(() => status('No homework yet.'));
  });

  it('a foreign ?student= falls back to the first, with no request for it', async () => {
    await open(`/my-homework?student=${FOREIGN}`);
    expect(labelled<HTMLSelectElement>(main(), 'Whose homework').value).toBe(
      CHILD_ID,
    );
    expect(api.requests.some((r) => r.path.includes(FOREIGN))).toBe(false);
  });

  it('an adult who has not shared: the consent message, no request', async () => {
    await open(`/my-homework?student=${ADULT_ID}`);
    expect(text()).toContain(
      "This student hasn't shared their academic records with you",
    );
    expect(api.requests.some((r) => r.path.includes(ADULT_ID))).toBe(false);
  });

  it('a student with their own login: plain text, no select', async () => {
    await open('/my-homework', {}, STUDENT);
    expect(main().querySelector('select')).toBeNull();
    expect(text()).toContain('Whose homework My homework');
  });

  it('no family students: the message', async () => {
    await open('/my-homework', {
      [FAMILY]: () => jsonResponse(200, { items: [] }),
    });
    expect(status("There's no student linked to your account.")).toBeTruthy();
  });
});

describe('the summary and the rows (E2, E3)', () => {
  it('four filter buttons with counts plus All, by aria-pressed', async () => {
    await open('/my-homework');
    const group = main().querySelector('[role="group"]')!;
    expect(group.getAttribute('aria-label')).toBe('Show homework by state');
    const pressed = () =>
      [...group.querySelectorAll('button')].map((b) => [
        b.textContent,
        b.getAttribute('aria-pressed'),
      ]);
    expect(pressed()).toEqual([
      ['Pending 1', 'false'],
      ['Missing 1', 'false'],
      ['Handed in 0', 'false'],
      ['Graded 1', 'false'],
      ['All', 'true'],
    ]);
    await click(button('Missing 1'));
    await settleUntil(() => listUrls.length === 2);
    expect(listUrls[1]!.searchParams.get('state')).toBe('missing');
    expect(app!.location()).toBe('/my-homework?state=missing');
    expect(pressed()[1]).toEqual(['Missing 1', 'true']);
  });

  it('rows link to the family detail with the student; deadline and grade texts', async () => {
    await open('/my-homework');
    const rows = [...main().querySelectorAll('li')];
    expect(rows).toHaveLength(3);
    expect(rows[0]!.querySelector('a')!.getAttribute('href')).toBe(
      `/my-homework/${ASSIGNMENT_ID}?student=${CHILD_ID}`,
    );
    expect(rows[0]!.textContent).toContain('Pending');
    expect(rows[0]!.textContent).toContain('Due Nov 12, 2030 at 4:00 PM');
    expect(rows[1]!.textContent).toContain(
      'Missing: the deadline was Oct 1, 2026',
    );
    expect(rows[2]!.textContent).toContain('Graded');
    expect(rows[2]!.textContent).toContain('8.00 of 10.00');
  });

  it('empty with a filter: "Show all" keeps focus on All', async () => {
    await open('/my-homework?state=submitted', {
      [listRoute(CHILD_ID)]: (request) => {
        listUrls.push(new URL(request.url));
        return jsonResponse(200, {
          ...STUDENT_LIST,
          items: new URL(request.url).searchParams.get('state')
            ? []
            : STUDENT_LIST.items,
        });
      },
    });
    expect(status('No homework matches this filter.')).toBeTruthy();
    await click(button('Show all'));
    await settleUntil(() => app!.location() === '/my-homework');
    await settleUntil(() => focused() === button('All'));
    await settleUntil(() => main().querySelectorAll('li').length === 3);
    expect(focused()).toBe(button('All'));
  });

  it('Load more follows the cursor', async () => {
    await open('/my-homework', {
      [listRoute(CHILD_ID)]: (request) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        listUrls.push(new URL(request.url));
        return jsonResponse(
          200,
          cursor
            ? { ...STUDENT_LIST, items: [CLOSED_ITEM], next_cursor: null }
            : { ...STUDENT_LIST, next_cursor: 'next' },
        );
      },
    });
    await click(button('Load more'));
    await settleUntil(() => main().querySelectorAll('li').length === 4);
    expect(listUrls[1]!.searchParams.get('cursor')).toBe('next');
  });

  it.each([
    [
      '403 student_consent_required',
      () => problemWith(403, 'student_consent_required'),
      "This student hasn't shared their academic records with you",
    ],
    [
      '404',
      () => problemWith(404, 'not_found'),
      "This homework list isn't available.",
    ],
  ])('%s: inline, the picker stays', async (_name, answer, message) => {
    await open('/my-homework', { [listRoute(CHILD_ID)]: answer });
    expect(text()).toContain(message);
    expect(labelled(main(), 'Whose homework')).toBeTruthy();
  });

  it('an error offers "Try again"', async () => {
    await open('/my-homework', {
      [listRoute(CHILD_ID)]: () => problemWith(500, 'internal_error'),
    });
    expect(button('Try again')).toBeTruthy();
  });
});

describe('the detail (E4)', () => {
  it('a guardian: read-only, "{child}\'s work", never the form', async () => {
    await open(`/my-homework/${ASSIGNMENT_ID}?student=${CHILD_ID}`);
    await settleUntil(() => heading()?.textContent === PENDING_ITEM.title);
    expect(text()).toContain('Set by Carmen Ruiz');
    expect(text()).toContain('Max score 10.00');
    expect(text()).toContain("Lucía Pérez's work");
    // The fixture says can_submit; the guardian still gets no form (E7).
    expect(main().querySelector('form')).toBeNull();
    expect(main().querySelector('textarea')).toBeNull();
    const back = main().querySelector<HTMLAnchorElement>(
      `a[href="/my-homework?student=${CHILD_ID}"]`,
    );
    expect(back?.textContent).toBe('Back to homework');
  });

  it('graded: score, percent, feedback, "Updated", "Handed in to the tutor"', async () => {
    await openSelf(GRADED_ITEM);
    expect(main().querySelector('form')).toBeNull();
    expect(text()).toContain('8.00 of 10.00 (80.00 %)');
    expect(text()).toContain('Buen trabajo.');
    expect(text()).toContain('Updated');
    expect(text()).toContain('Handed in to the tutor');
    expect(text()).toContain('Graded on Nov 12, 2030');
  });

  it('feedback only: no score', async () => {
    await openSelf(FEEDBACK_ITEM);
    expect(text()).toContain('Feedback only');
    expect(text()).toContain('Sigue así.');
    expect(text()).not.toMatch(/of 10\.00/);
  });

  it('handed in after the deadline: closed, no form', async () => {
    await openSelf(CLOSED_ITEM);
    expect(main().querySelector('form')).toBeNull();
    expect(text()).toContain('Handing in closed on Oct 1, 2026, 4:00 PM.');
    expect(text()).toContain('Late');
  });

  it('plain text everywhere; an unsafe link as text, a long one wraps', async () => {
    await openSelf(
      studentItem({
        title: SCRIPT,
        instructions: SCRIPT,
        submission: submission({
          state: 'graded',
          submitted_at: '2030-11-11T18:00:00Z',
          submitted_via: 'student',
          text: SCRIPT,
          url: 'javascript:alert(1)',
          score: '8.00',
          score_percent: '80.00',
          feedback: SCRIPT,
          graded_at: '2030-11-12T18:00:00Z',
          version: 3,
        }),
        can_submit: false,
      }),
    );
    expect(main().querySelector('script')).toBeNull();
    expect(text().split(SCRIPT).length - 1).toBeGreaterThanOrEqual(4);
    expect(main().querySelector('a[href^="javascript"]')).toBeNull();
    expect(text()).toContain('javascript:alert(1)');
    app!.unmount();
    document.body.innerHTML = '';
    await openSelf({
      ...SUBMITTED_ITEM,
      submission: { ...SUBMITTED_ITEM.submission, url: LONG_URL },
    });
    const anchor = main().querySelector<HTMLAnchorElement>(
      `a[href="${LONG_URL}"]`,
    )!;
    expect(anchor.className).toContain('break-all');
    expect(anchor.rel).toBe('noopener noreferrer nofollow');
  });

  it('a 404: "no longer available", inline, with a link to /my-homework', async () => {
    await open(`/my-homework/${ASSIGNMENT_ID}?student=${CHILD_ID}`, {
      [itemRoute(CHILD_ID)]: () => problemWith(404, 'not_found'),
    });
    await settleUntil(() => status('This homework is no longer available.'));
    expect(main().querySelector('a[href="/my-homework"]')?.textContent).toBe(
      'See all homework',
    );
  });

  it('a 403 student_consent_required: the consent message', async () => {
    await open(`/my-homework/${ASSIGNMENT_ID}?student=${CHILD_ID}`, {
      [itemRoute(CHILD_ID)]: () => problemWith(403, 'student_consent_required'),
    });
    await settleUntil(() =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
  });

  it('an adult who has not shared (picker): the consent message, no request', async () => {
    await open(`/my-homework/${ASSIGNMENT_ID}?student=${ADULT_ID}`);
    await settleUntil(() =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
    expect(api.requests.some((r) => r.path.includes(ADULT_ID))).toBe(false);
  });
});

describe('the hand-in form (E6)', () => {
  it('fields, help text and "Hand in"', async () => {
    await openSelf(PENDING_ITEM);
    expect(text()).toContain('Your work');
    expect(text()).toContain(
      'You can replace your work until Nov 12, 2030, 4:00 PM.',
    );
    const link = field<HTMLInputElement>('Link (optional)');
    expect(link.type).toBe('url');
    expect(link.inputMode).toBe('url');
    expect(link.autocomplete).toBe('url');
    expect(button('Hand in')).toBeTruthy();
  });

  it('a missing item: the late help', async () => {
    await openSelf({ ...MISSING_ITEM, id: ASSIGNMENT_ID });
    expect(text()).toContain(
      'The deadline has passed. Your work will be marked late.',
    );
  });

  it('nothing typed: the form error, focus on Text, nothing sent', async () => {
    await openSelf(PENDING_ITEM);
    setFieldValue(field('Text (optional)'), '   ');
    await click(button('Hand in'));
    await settleUntil(() =>
      text().includes('Write your answer or add a link.'),
    );
    const area = field('Text (optional)');
    await settleUntil(() => focused() === area);
    expect(describedText(area)).toContain('Write your answer or add a link.');
    expect(api.calls(submitRoute(STUDENT_ID))).toHaveLength(0);
  });

  it('a bad link: on the field, focused, nothing sent', async () => {
    await openSelf(PENDING_ITEM);
    setFieldValue(field('Link (optional)'), 'javascript:x');
    await click(button('Hand in'));
    const link = field('Link (optional)');
    await settleUntil(() => focused() === link);
    expect(describedText(link)).toBe(
      'Use a full link starting with http:// or https://',
    );
    expect(api.calls(submitRoute(STUDENT_ID))).toHaveLength(0);
  });

  it('a first hand-in: no version; the notice; focus on "Your work"', async () => {
    await openSelf(PENDING_ITEM, {
      [submitRoute(STUDENT_ID)]: () => jsonResponse(200, SUBMITTED_ITEM),
    });
    setFieldValue(field('Text (optional)'), '  Mi respuesta  ');
    await click(button('Hand in'));
    await settleUntil(() => api.calls(submitRoute(STUDENT_ID)).length === 1);
    expect(api.calls(submitRoute(STUDENT_ID))[0]!.body).toEqual({
      text: 'Mi respuesta',
      url: null,
    });
    await settleUntil(() => status('Work handed in.'));
    const work = [...main().querySelectorAll('h2')].find(
      (h) => h.textContent === 'Your work',
    )!;
    await settleUntil(() => focused() === work);
    // The form now replaces, starting from the stored work.
    expect(button('Replace my work')).toBeTruthy();
    expect(field<HTMLTextAreaElement>('Text (optional)').value).toBe(
      'Mi respuesta',
    );
  });

  it('a replacement: prefilled, with the version; "Work replaced."', async () => {
    await openSelf(SUBMITTED_ITEM, {
      [submitRoute(STUDENT_ID)]: () =>
        jsonResponse(200, {
          ...SUBMITTED_ITEM,
          submission: { ...SUBMITTED_ITEM.submission, version: 3 },
        }),
    });
    expect(field<HTMLTextAreaElement>('Text (optional)').value).toBe(
      'Mi respuesta',
    );
    expect(field<HTMLInputElement>('Link (optional)').value).toBe(
      'https://example.com/mi-trabajo',
    );
    setFieldValue(field('Link (optional)'), '');
    await click(button('Replace my work'));
    await settleUntil(() => api.calls(submitRoute(STUDENT_ID)).length === 1);
    expect(api.calls(submitRoute(STUDENT_ID))[0]!.body).toEqual({
      version: 2,
      text: 'Mi respuesta',
      url: null,
    });
    await settleUntil(() => status('Work replaced.'));
    const work = [...main().querySelectorAll('h2')].find(
      (h) => h.textContent === 'Your work',
    )!;
    await settleUntil(() => focused() === work);
  });

  it.each([
    [
      'submission_empty',
      ['body'],
      'Write your answer or add a link.',
      'Text (optional)',
    ],
    [
      'url_invalid',
      ['body', 'url'],
      'Use a full link starting with http:// or https://',
      'Link (optional)',
    ],
    [
      'too_long',
      ['body', 'text'],
      'The text can have at most 10000 characters.',
      'Text (optional)',
    ],
  ] as const)(
    '422 %s: on its field, focused',
    async (code, path, message, label) => {
      await openSelf(PENDING_ITEM, {
        [submitRoute(STUDENT_ID)]: () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: [...path], code }],
          }),
      });
      setFieldValue(field('Text (optional)'), 'x');
      await click(button('Hand in'));
      await settleUntil(() => text().includes(message));
      await settleUntil(() => focused() === field(label));
    },
  );

  it.each([
    [
      'submission_locked',
      409,
      'The deadline has passed; your work can no longer be replaced.',
    ],
    ['submission_graded', 409, 'This work has been graded.'],
    ['version_conflict', 409, 'This changed elsewhere.'],
  ] as const)(
    '%s: the message and Reload, focused',
    async (code, http, message) => {
      await openSelf(SUBMITTED_ITEM, {
        [submitRoute(STUDENT_ID)]: () => problemWith(http, code),
      });
      await click(button('Replace my work'));
      await settleUntil(() => text().includes(message));
      await settleUntil(() => focused() === failure());
      expect(button('Reload')).toBeTruthy();
    },
  );

  it('422 version_required: "This changed elsewhere."', async () => {
    await openSelf(SUBMITTED_ITEM, {
      [submitRoute(STUDENT_ID)]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'version'], code: 'version_required' }],
        }),
    });
    await click(button('Replace my work'));
    await settleUntil(() => text().includes('This changed elsewhere.'));
    await settleUntil(() => focused() === failure());
  });

  it('Reload refetches, then focuses the heading', async () => {
    await openSelf(SUBMITTED_ITEM, {
      [submitRoute(STUDENT_ID)]: () => problemWith(409, 'submission_locked'),
      [itemRoute(STUDENT_ID)]: [
        () => jsonResponse(200, SUBMITTED_ITEM),
        () => jsonResponse(200, CLOSED_ITEM),
      ],
    });
    await click(button('Replace my work'));
    await settleUntil(() => focused() === failure());
    await click(button('Reload'));
    await settleUntil(() => api.calls(itemRoute(STUDENT_ID)).length === 2);
    await settleUntil(() => text().includes('Handing in closed on'));
    await settleUntil(() => focused() === heading());
    expect(main().querySelector('form')).toBeNull();
  });

  it('a 404 on the hand-in: "no longer available"', async () => {
    await openSelf(PENDING_ITEM, {
      [submitRoute(STUDENT_ID)]: () => problemWith(404, 'not_found'),
    });
    setFieldValue(field('Text (optional)'), 'x');
    await click(button('Hand in'));
    await settleUntil(() => status('This homework is no longer available.'));
  });
});
