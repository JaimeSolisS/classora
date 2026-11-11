import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { fieldByLabel, pickOption, typeInto, click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { SubjectOption } from './api';
import {
  ANA,
  CLASSROOMS,
  CREATED,
  GENERAL_SUBJECT,
  SUBJECTS,
  TUTORS,
} from './fixtures';
import {
  EMPTY_SCHEDULE,
  generalSubjectId,
  newSessionValues,
  prefillValues,
} from './rules';

// HF-12 (#597 AC6): "Schedule a session" (Individual, also from a calendar
// range select) opens with the built-in General subject selected; it can be
// changed and the chosen subject is sent; without General in the options the
// subject starts empty, as before.

const CREATE = 'POST /api/v1/class-sessions';
const SCHEDULER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'schedule.manage', 'student.view'],
};

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

function subjectItems(subjects: readonly SubjectOption[]) {
  return subjects.map((s) => ({
    id: s.id,
    name: s.name,
    education_level_id: '00000000-0000-4000-8000-0000000004f1',
    education_level_name: s.levelName,
    is_active: true,
    is_general: s.isGeneral ?? false,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }));
}

const WITH_GENERAL = [GENERAL_SUBJECT, ...SUBJECTS];

function stub(
  subjects: readonly SubjectOption[] = WITH_GENERAL,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(SCHEDULER),
    'GET /api/v1/subjects': page(subjectItems(subjects)),
    'GET /api/v1/tutors': page(
      TUTORS.map((t) => ({
        user_id: t.id,
        username: t.id.slice(-4),
        display_name: t.name,
        state: 'active',
        email: null,
        phone: null,
      })),
    ),
    'GET /api/v1/classrooms': page(
      CLASSROOMS.map((c) => ({
        id: c.id,
        name: c.name,
        capacity: 10,
        is_active: true,
        availability: [],
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })),
    ),
    'GET /api/v1/students': page([
      {
        id: ANA.id,
        given_names: 'Ana',
        family_names: 'López',
        is_active: true,
        is_minor: true,
        school_grade: null,
        education_level: null,
      },
    ]),
    ...routes,
  });
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function subjectSelect(): HTMLSelectElement {
  return [...main().querySelectorAll('label')]
    .filter((l) => l.textContent === 'Subject')
    .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
}

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function fillTheRest() {
  await pickOption(main(), 'Student', 'Ana', 'Ana López');
  typeInto(fieldByLabel(main(), 'Date'), '2031-01-06');
  typeInto(fieldByLabel(main(), 'Start'), '10:00');
  typeInto(fieldByLabel(main(), 'End'), '11:00');
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the General subject rules (HF-12)', () => {
  it('finds General among the options, or nothing', () => {
    expect(generalSubjectId(WITH_GENERAL)).toBe(GENERAL_SUBJECT.id);
    expect(generalSubjectId(SUBJECTS)).toBe('');
    expect(generalSubjectId([])).toBe('');
  });

  it('preselects General over the plain form and over a pre-fill', () => {
    expect(newSessionValues(null, WITH_GENERAL)).toEqual({
      ...EMPTY_SCHEDULE,
      subjectId: GENERAL_SUBJECT.id,
    });
    const prefill = prefillValues(
      new URLSearchParams('date=2031-03-10&start=10:00&end=11:00'),
      { tutors: TUTORS, classrooms: CLASSROOMS },
    );
    expect(newSessionValues(prefill, WITH_GENERAL)).toEqual({
      ...prefill,
      subjectId: GENERAL_SUBJECT.id,
    });
    expect(newSessionValues(null, SUBJECTS)).toEqual(EMPTY_SCHEDULE);
  });
});

describe('Schedule a session with General (HF-12)', () => {
  it('opens with General selected and sends it', async () => {
    const api = stub(WITH_GENERAL, {
      [CREATE]: () => jsonResponse(201, CREATED),
    });
    app = await renderApp('/schedule/new');

    expect(subjectSelect().value).toBe(GENERAL_SUBJECT.id);
    await fillTheRest();
    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toMatchObject({
      kind: 'individual',
      subject_id: GENERAL_SUBJECT.id,
    });
    // "Schedule another" starts with General again.
    await click(buttonNamed(main(), 'Schedule another'));
    expect(subjectSelect().value).toBe(GENERAL_SUBJECT.id);
  });

  it('can be changed, and the chosen subject is sent', async () => {
    const api = stub(WITH_GENERAL, {
      [CREATE]: () => jsonResponse(201, CREATED),
    });
    app = await renderApp('/schedule/new');

    choose(subjectSelect(), SUBJECTS[1]!.id);
    await fillTheRest();
    await click(buttonNamed(main(), 'Schedule session'));

    expect(api.calls(CREATE)[0]!.body).toMatchObject({
      subject_id: SUBJECTS[1]!.id,
    });
  });

  it('is preselected from a calendar range select too', async () => {
    stub();
    app = await renderApp(
      '/schedule/new?date=2031-03-10&start=10:00&end=11:00',
    );

    expect(subjectSelect().value).toBe(GENERAL_SUBJECT.id);
    expect(fieldByLabel(main(), 'Date').value).toBe('2031-03-10');
  });

  it('starts empty without General in the options', async () => {
    stub(SUBJECTS);
    app = await renderApp('/schedule/new');

    expect(subjectSelect().value).toBe('');
  });
});
