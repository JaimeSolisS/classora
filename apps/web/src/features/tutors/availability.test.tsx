import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  noContent,
  problem,
  stubApi,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { TutorAvailability, TutorDetail } from './api';
import {
  exceptionBody,
  EMPTY_EXCEPTION,
  validateException,
} from './availabilityRules';
import {
  AVAILABILITY,
  EXCEPTIONS,
  NO_AVAILABILITY,
  TUTOR_AS_ADMIN,
  TUTOR_AS_SELF,
  TUTOR_ID,
  TUTOR_ITEM,
  TUTOR_READ_ONLY,
} from './fixtures';

// #45 F1-F3: the Availability section of the tutor profile: edit rights
// (the tutor themselves, an Administrator, a Coordinator read-only), the
// weekly PUT body, the exception dialog's checks (all day vs hours, extra
// availability needs hours), deleting, and the error messages.

const DETAIL = `GET /api/v1/tutors/${TUTOR_ID}`;
const AVAILABILITY_ROUTE = `GET /api/v1/tutors/${TUTOR_ID}/availability`;
const WEEKLY = `PUT /api/v1/tutors/${TUTOR_ID}/availability/weekly`;
const EXCEPTIONS_ROUTE = `POST /api/v1/tutors/${TUTOR_ID}/availability/exceptions`;
const DELETE = `DELETE /api/v1/tutors/${TUTOR_ID}/availability/exceptions/${EXCEPTIONS[0]!.id}`;

const ADMIN: SessionUserBody = {
  ...TEST_USER,
  roles: ['administrator'],
  permissions: ['tutor.view', 'tutor.manage'],
};

const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['tutor.view'],
};

const SELF: SessionUserBody = {
  ...TEST_USER,
  id: TUTOR_ID,
  username: TUTOR_ITEM.username,
  display_name: TUTOR_ITEM.display_name,
  roles: ['tutor'],
  permissions: ['student.view', 'tutor.view'],
};

let app: RenderedApp | undefined;

const section = () => document.getElementById('availability')!;

function stub(
  user: SessionUserBody,
  detail: TutorDetail,
  availability: TutorAvailability = AVAILABILITY,
  routes: Record<string, Responder | Responder[]> = {},
) {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [DETAIL]: () => jsonResponse(200, detail),
    [`GET /api/v1/tutors/${TUTOR_ID}/qualifications`]: () =>
      jsonResponse(200, []),
    [AVAILABILITY_ROUTE]: () => jsonResponse(200, availability),
    ...routes,
  });
}

function buttonTexts(root: ParentNode): string[] {
  return [...root.querySelectorAll('button')].map(
    (button) => button.textContent ?? '',
  );
}

function byLabel(root: ParentNode, label: string): HTMLInputElement {
  const field = root.querySelector<HTMLInputElement>(`[aria-label="${label}"]`);
  if (!field) {
    throw new Error(`No field with aria-label "${label}"`);
  }
  return field;
}

function labelled<T extends HTMLElement = HTMLInputElement>(
  root: ParentNode,
  text: string,
): T {
  const label = [...root.querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  );
  const field = label?.htmlFor ? document.getElementById(label.htmlFor) : null;
  if (!field) {
    throw new Error(`No field labelled "${text}"`);
  }
  return field as T;
}

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
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
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('the Availability section (F1, F2)', () => {
  it('names the center time zone and lists the exceptions', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN);

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(section().textContent).toContain(
      "Times are in the center's time zone (America/Mexico_City).",
    );
    const rows = [...section().querySelectorAll('tbody tr')];
    const cells = (row: Element) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent);
    expect(cells(rows[0]!)[0]).toBe('Time off');
    expect(cells(rows[0]!)[2]).toBe('All day');
    expect(cells(rows[0]!)[3]).toBe('Vacaciones');
    expect(cells(rows[1]!)[0]).toBe('Extra availability');
    expect(cells(rows[1]!)[2]).toBe('10:00–12:00');
  });

  it('a Coordinator sees the weekly list read-only, without actions', async () => {
    stub(COORDINATOR, TUTOR_READ_ONLY);

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(section().querySelector('fieldset')).toBeNull();
    const terms = [...section().querySelectorAll('dt')].map(
      (dt) => dt.textContent,
    );
    const definitions = [...section().querySelectorAll('dd')].map(
      (dd) => dd.textContent,
    );
    expect(terms).toEqual(['Monday', 'Wednesday']);
    expect(definitions).toEqual(['16:00–20:00', '16:00–18:00, 18:00–19:00']);
    expect(buttonTexts(section())).toEqual([]);
  });

  it.each([
    ['the tutor themselves', SELF, TUTOR_AS_SELF],
    ['an Administrator', ADMIN, TUTOR_AS_ADMIN],
  ])('%s edits it', async (_name, user, detail) => {
    stub(user, detail);

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(section().querySelector('fieldset')).not.toBeNull();
    const names = buttonTexts(section());
    expect(names).toContain('Save availability');
    expect(names).toContain('Add exception');
    expect(names.filter((name) => name === 'Delete')).toHaveLength(2);
  });

  it('shows the empty states', async () => {
    stub(COORDINATOR, TUTOR_READ_ONLY, NO_AVAILABILITY);

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(section().textContent).toContain(
      'No weekly availability yet. Without it the tutor is never available.',
    );
    expect(section().textContent).toContain('No exceptions.');
  });
});

describe('saving the weekly windows (F1, F3)', () => {
  it('sends the full list with the loaded version', async () => {
    const saved = {
      ...AVAILABILITY,
      weekly: [
        ...AVAILABILITY.weekly,
        { weekday: 3, starts_at: '09:00', ends_at: '11:00' },
      ],
      version: 3,
    };
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [WEEKLY]: () => jsonResponse(200, saved),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Add window'));
    // The new row copies the last row's weekday (Wednesday).
    typeInto(byLabel(section(), 'Window 4 start time'), '09:00');
    typeInto(byLabel(section(), 'Window 4 end time'), '11:00');
    await click(buttonNamed(section(), 'Save availability'));

    expect(api.calls(WEEKLY).map((r) => r.body)).toEqual([
      {
        version: 2,
        windows: [
          ...AVAILABILITY.weekly,
          { weekday: 3, starts_at: '09:00', ends_at: '11:00' },
        ],
      },
    ]);
    expect(section().textContent).toContain('Availability saved.');
  });

  it('unchanged windows send nothing', async () => {
    const api = stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Save availability'));

    expect(api.calls(WEEKLY)).toHaveLength(0);
    expect(section().textContent).toContain('No changes to save.');
  });

  it('an overlap is caught before sending', async () => {
    const api = stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Add window'));
    typeInto(byLabel(section(), 'Window 4 start time'), '17:00');
    typeInto(byLabel(section(), 'Window 4 end time'), '18:30');
    await click(buttonNamed(section(), 'Save availability'));

    expect(api.calls(WEEKLY)).toHaveLength(0);
    expect(section().textContent).toContain(
      'This window overlaps another window on the same day.',
    );
  });

  it('version_conflict offers Reload, which refetches', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, NO_AVAILABILITY, {
      [WEEKLY]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Add window'));
    typeInto(byLabel(section(), 'Window 1 start time'), '09:00');
    typeInto(byLabel(section(), 'Window 1 end time'), '11:00');

    await click(buttonNamed(section(), 'Save availability'));
    const before = api.calls(AVAILABILITY_ROUTE).length;
    await click(buttonNamed(section(), 'Reload'));

    expect(api.calls(AVAILABILITY_ROUTE).length).toBe(before + 1);
  });

  it('availability_change_requires_approval shows in the alert region', async () => {
    stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [WEEKLY]: () => problem(409, 'availability_change_requires_approval'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Remove window 1'));
    await click(buttonNamed(section(), 'Save availability'));

    expect(alerts(section()).join(' ')).toContain(
      'This change affects scheduled classes and needs approval.',
    );
  });
});

describe('the exception dialog (F1, F3)', () => {
  it('an all-day time off sends no times', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [EXCEPTIONS_ROUTE]: () => jsonResponse(201, EXCEPTIONS[0]),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Add exception'));
    const dialog = openDialog()!;
    // All day is on by default: no time fields.
    expect(dialog.querySelector('input[type="time"]')).toBeNull();
    typeInto(labelled(dialog, 'Start date'), '2026-12-21');
    typeInto(labelled(dialog, 'End date'), '2026-12-31');
    typeInto(labelled(dialog, 'Note (optional)'), ' Vacaciones ');
    await click(buttonNamed(dialog, 'Add'));

    expect(api.calls(EXCEPTIONS_ROUTE).map((r) => r.body)).toEqual([
      {
        kind: 'unavailable',
        starts_on: '2026-12-21',
        ends_on: '2026-12-31',
        note: 'Vacaciones',
      },
    ]);
    expect(openDialog()).toBeNull();
    expect(section().textContent).toContain('Exception added.');
  });

  it('extra availability forces hours and checks them', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [EXCEPTIONS_ROUTE]: () => jsonResponse(201, EXCEPTIONS[1]),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Add exception'));
    const dialog = openDialog()!;

    choose(labelled<HTMLSelectElement>(dialog, 'Type'), 'available');
    const allDay =
      dialog.querySelector<HTMLButtonElement>('[role="checkbox"]')!;
    expect(allDay.disabled).toBe(true);
    expect(allDay.getAttribute('aria-checked')).toBe('false');
    typeInto(labelled(dialog, 'Start date'), '2027-01-09');
    typeInto(labelled(dialog, 'End date'), '2027-01-09');
    await click(buttonNamed(dialog, 'Add'));

    expect(api.calls(EXCEPTIONS_ROUTE)).toHaveLength(0);
    expect(dialog.textContent).toContain('Enter a time (HH:MM).');

    typeInto(labelled(dialog, 'Start time'), '12:00');
    typeInto(labelled(dialog, 'End time'), '10:00');
    expect(dialog.textContent).toContain(
      'The end time must be after the start time.',
    );
    typeInto(labelled(dialog, 'End time'), '13:00');
    await click(buttonNamed(dialog, 'Add'));

    expect(api.calls(EXCEPTIONS_ROUTE).map((r) => r.body)).toEqual([
      {
        kind: 'available',
        starts_on: '2027-01-09',
        ends_on: '2027-01-09',
        starts_at: '12:00',
        ends_at: '13:00',
      },
    ]);
  });

  it('partial time off: unticking All day shows the times', async () => {
    stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Add exception'));
    const dialog = openDialog()!;

    await click(dialog.querySelector<HTMLButtonElement>('[role="checkbox"]')!);

    expect(dialog.querySelectorAll('input[type="time"]')).toHaveLength(2);
  });

  it('a requires-approval answer stays in the dialog', async () => {
    stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [EXCEPTIONS_ROUTE]: () =>
        problem(409, 'availability_change_requires_approval'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Add exception'));
    const dialog = openDialog()!;
    typeInto(labelled(dialog, 'Start date'), '2026-12-21');
    typeInto(labelled(dialog, 'End date'), '2026-12-21');

    await click(buttonNamed(dialog, 'Add'));

    expect(openDialog()).not.toBeNull();
    expect(alerts(openDialog()!).join(' ')).toContain(
      'This change affects scheduled classes and needs approval.',
    );
  });

  it('the rules: dates, span, hours and the body', () => {
    const base = { ...EMPTY_EXCEPTION, startsOn: '2026-12-21' };
    expect(validateException(EMPTY_EXCEPTION)).toEqual({
      startsOn: 'tutors.availability.validation.dateRequired',
      endsOn: 'tutors.availability.validation.dateRequired',
    });
    expect(validateException({ ...base, endsOn: '2026-12-20' }).endsOn).toBe(
      'tutors.availability.validation.endsOnAfterStart',
    );
    expect(
      validateException({
        ...base,
        startsOn: '2027-01-01',
        endsOn: '2028-01-03',
      }).endsOn,
    ).toBe('tutors.availability.validation.rangeTooLong');
    expect(
      validateException({
        ...base,
        startsOn: '2027-01-01',
        endsOn: '2028-01-02',
      }),
    ).toEqual({});
    expect(
      validateException({
        ...base,
        endsOn: '2026-12-21',
        note: 'x'.repeat(201),
      }).note,
    ).toBe('tutors.availability.validation.noteTooLong');
    // A whole-day value ignores stale times.
    expect(
      exceptionBody({
        ...base,
        endsOn: '2026-12-21',
        startsAt: '10:00',
        endsAt: '11:00',
      }),
    ).toEqual({
      kind: 'unavailable',
      starts_on: '2026-12-21',
      ends_on: '2026-12-21',
    });
  });
});

describe('deleting an exception (F1, F3)', () => {
  it('asks first, then deletes and refetches', async () => {
    const api = stub(ADMIN, TUTOR_AS_ADMIN, AVAILABILITY, {
      [DELETE]: () => noContent(),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const deletes = [...section().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Delete',
    );
    await click(deletes[0]!);
    expect(openDialog()!.textContent).toContain('Time off');
    const before = api.calls(AVAILABILITY_ROUTE).length;
    await click(buttonNamed(openDialog()!, 'Delete'));

    expect(api.calls(DELETE)).toHaveLength(1);
    expect(openDialog()).toBeNull();
    expect(section().textContent).toContain('Exception deleted.');
    expect(api.calls(AVAILABILITY_ROUTE).length).toBeGreaterThan(before);
  });

  it('Keep closes without a call', async () => {
    const api = stub(ADMIN, TUTOR_AS_ADMIN);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const deletes = [...section().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Delete',
    );
    await click(deletes[0]!);
    await click(buttonNamed(openDialog()!, 'Keep'));

    expect(openDialog()).toBeNull();
    expect(api.calls(DELETE)).toHaveLength(0);
  });

  it('an error shows in the section alert', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN, AVAILABILITY, {
      [DELETE]: () => problem(409, 'availability_change_requires_approval'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const deletes = [...section().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Delete',
    );
    await click(deletes[0]!);
    await click(buttonNamed(openDialog()!, 'Delete'));

    expect(alerts(section()).join(' ')).toContain(
      'This change affects scheduled classes and needs approval.',
    );
  });
});
