import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
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
  settingsBody,
  settingsValues,
  validateSettings,
} from './availabilitySettingsRules';
import { describeChange } from './changeRequests';
import {
  AVAILABILITY,
  AVAILABILITY_WITH_CHANGES,
  REJECTED_CHANGE,
  TIME_OFF_CHANGE,
  WEEKLY_CHANGE,
  CLASSROOM_OPTIONS,
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
// availability needs hours), deleting, and the error messages. #63 H1: a
// 202 "Sent for approval", the pending changes, Withdraw and its 409.

const DETAIL = `GET /api/v1/tutors/${TUTOR_ID}`;
const AVAILABILITY_ROUTE = `GET /api/v1/tutors/${TUTOR_ID}/availability`;
const WEEKLY = `PUT /api/v1/tutors/${TUTOR_ID}/availability/weekly`;
const EXCEPTIONS_ROUTE = `POST /api/v1/tutors/${TUTOR_ID}/availability/exceptions`;
const SETTINGS = `PATCH /api/v1/tutors/${TUTOR_ID}/availability/settings`;
const CLASSROOMS = 'GET /api/v1/classrooms';
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

const WEEKLY_TEXT =
  'Weekly availability: Monday 16:00–18:00; Wednesday 16:00–19:00';

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
    const weekly = section().querySelector('dl')!;
    const terms = [...weekly.querySelectorAll('dt')].map(
      (dt) => dt.textContent,
    );
    const definitions = [...weekly.querySelectorAll('dd')].map(
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

  it('a 202 announces "Sent for approval" and keeps the current windows (#63)', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [WEEKLY]: () => jsonResponse(202, WEEKLY_CHANGE),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    const before = api.calls(AVAILABILITY_ROUTE).length;

    await click(buttonNamed(section(), 'Remove window 1'));
    await click(buttonNamed(section(), 'Save availability'));

    const status = [...section().querySelectorAll('[role="status"]')]
      .map((node) => node.textContent)
      .join(' ');
    expect(status).toContain(
      'Sent for approval: this change affects 5 scheduled classes.',
    );
    expect(status).toContain('and 2 more');
    expect(status).not.toContain('Availability saved.');
    expect(alerts(section())).toEqual([]);
    // The rows show the current windows again (3, as loaded).
    expect(byLabel(section(), 'Window 3 start time').value).toBe('18:00');
    expect(api.calls(AVAILABILITY_ROUTE).length).toBeGreaterThan(before);
  });
});

describe('pending changes (#63 H1)', () => {
  const CANCEL = `POST /api/v1/tutors/${TUTOR_ID}/availability/change-requests/${WEEKLY_CHANGE.id}/cancel`;

  it('lists the pending and the decided changes with the reason', async () => {
    stub(COORDINATOR, TUTOR_READ_ONLY, AVAILABILITY_WITH_CHANGES);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const text = section().textContent ?? '';
    expect(text).toContain('Changes waiting for approval');
    expect(text).toContain('5 scheduled classes affected');
    expect(text).toContain('1 scheduled class affected');
    expect(text).toContain('Sent by Laura Admin');
    expect(text).toContain('Reason: No hay sustituto para esas clases.');
    expect(buttonTexts(section())).not.toContain('Withdraw');
  });

  it('Withdraw asks first, sends the version and refetches', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY_WITH_CHANGES, {
      [CANCEL]: () =>
        jsonResponse(200, { ...WEEKLY_CHANGE, status: 'cancelled' }),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    const before = api.calls(AVAILABILITY_ROUTE).length;

    await click(buttonNamed(section(), `Withdraw the change ${WEEKLY_TEXT}`));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('will no longer wait for approval');
    await click(buttonNamed(dialog, 'Withdraw'));

    expect(api.calls(CANCEL).map((call) => call.body)).toEqual([
      { version: 1 },
    ]);
    expect(openDialog()).toBeNull();
    expect(section().textContent).toContain('Change withdrawn.');
    expect(api.calls(AVAILABILITY_ROUTE).length).toBeGreaterThan(before);
  });

  it('a 409 shows in the Withdraw dialog', async () => {
    stub(SELF, TUTOR_AS_SELF, AVAILABILITY_WITH_CHANGES, {
      [CANCEL]: () => problem(409, 'availability_change_not_pending'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), `Withdraw the change ${WEEKLY_TEXT}`));
    await click(buttonNamed(openDialog()!, 'Withdraw'));

    expect(alerts(openDialog()!).join(' ')).toContain(
      'This change request is no longer pending.',
    );
  });

  it('describes each kind of change', () => {
    const date = (value: string) => value;
    expect(describeChange(WEEKLY_CHANGE, i18n.t, date)).toBe(WEEKLY_TEXT);
    expect(describeChange(TIME_OFF_CHANGE, i18n.t, date)).toBe(
      'Time off: 2027-01-11 – 2027-01-15',
    );
    expect(describeChange(REJECTED_CHANGE, i18n.t, date)).toBe(
      'Remove extra availability: 2027-01-09, 10:00–12:00',
    );
    expect(
      describeChange(
        { kind: 'weekly', change: { windows: [], exception: null } },
        i18n.t,
        date,
      ),
    ).toBe('Weekly availability: no hours');
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

  it('time off sent for approval (202) closes the dialog and says so', async () => {
    stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [EXCEPTIONS_ROUTE]: () => jsonResponse(202, TIME_OFF_CHANGE),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Add exception'));
    const dialog = openDialog()!;
    typeInto(labelled(dialog, 'Start date'), '2027-01-11');
    typeInto(labelled(dialog, 'End date'), '2027-01-15');

    await click(buttonNamed(dialog, 'Add'));

    expect(openDialog()).toBeNull();
    expect(section().textContent).toContain(
      'Sent for approval: this change affects 1 scheduled class.',
    );
    expect(section().textContent).not.toContain('Exception added.');
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
      [DELETE]: () => problem(404, 'not_found'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const deletes = [...section().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Delete',
    );
    await click(deletes[0]!);
    await click(buttonNamed(openDialog()!, 'Delete'));

    expect(alerts(section())).not.toEqual([]);
  });

  it('a delete sent for approval (202) says so (#63)', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN, AVAILABILITY, {
      [DELETE]: () => jsonResponse(202, REJECTED_CHANGE),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const deletes = [...section().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Delete',
    );
    await click(deletes[0]!);
    await click(buttonNamed(openDialog()!, 'Delete'));

    expect(section().textContent).toContain(
      'Sent for approval: this change affects 2 scheduled classes.',
    );
    expect(section().textContent).not.toContain('Exception deleted.');
  });
});

// --- #197: teaching limits and preferred classrooms -------------------------

function group(root: ParentNode, legend: string): HTMLFieldSetElement {
  const found = [...root.querySelectorAll('fieldset')].find(
    (candidate) => candidate.querySelector('legend')?.textContent === legend,
  );
  if (!found) {
    throw new Error(`No fieldset "${legend}"`);
  }
  return found;
}

function checkboxFor(root: ParentNode, text: string): HTMLButtonElement {
  return labelled<HTMLButtonElement>(root, text);
}

function classroomsPage() {
  return jsonResponse(200, {
    items: CLASSROOM_OPTIONS.map((room) => ({
      ...room,
      capacity: 10,
      is_active: true,
      availability: [],
      version: 1,
      created_at: '2026-10-01T15:00:00Z',
      updated_at: '2026-10-01T15:00:00Z',
    })),
    total: CLASSROOM_OPTIONS.length,
    limit: 100,
    offset: 0,
  });
}

describe('the teaching limits and preferred classrooms (#197 C1, C2)', () => {
  it('sends only the changed fields with the loaded version', async () => {
    const saved = {
      ...AVAILABILITY,
      limits: { ...AVAILABILITY.limits, daily_limit_minutes: 270 },
      version: 3,
    };
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [CLASSROOMS]: classroomsPage,
      [SETTINGS]: () => jsonResponse(200, saved),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const daily = group(section(), 'Daily maximum');
    expect(labelled(daily, 'Hours').value).toBe('4');
    expect(labelled(daily, 'Minutes').value).toBe('0');
    typeInto(labelled(daily, 'Minutes'), '30');
    await click(checkboxFor(section(), 'Aula 2'));
    await click(checkboxFor(section(), 'Aula Vieja (inactive)'));
    await click(buttonNamed(section(), 'Save limits and classrooms'));

    expect(api.calls(SETTINGS).map((r) => r.body)).toEqual([
      {
        version: 2,
        daily_limit_minutes: 270,
        preferred_classroom_ids: [
          '00000000-0000-4000-8000-0000000000c1',
          '00000000-0000-4000-8000-0000000000c2',
        ],
      },
    ]);
    expect(section().textContent).toContain('Limits and classrooms saved.');
    expect(api.calls(CLASSROOMS)).toHaveLength(1);
  });

  it('"No limit" sends null and unchanged values send nothing', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [CLASSROOMS]: classroomsPage,
      [SETTINGS]: () => jsonResponse(200, AVAILABILITY),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Save limits and classrooms'));
    expect(api.calls(SETTINGS)).toHaveLength(0);
    expect(section().textContent).toContain('No changes to save.');

    await click(checkboxFor(group(section(), 'Weekly maximum'), 'No limit'));
    await click(
      checkboxFor(
        group(section(), 'Required break between classes'),
        'No break',
      ),
    );
    await click(buttonNamed(section(), 'Save limits and classrooms'));

    expect(api.calls(SETTINGS).map((r) => r.body)).toEqual([
      { version: 2, weekly_limit_minutes: null, required_break_minutes: null },
    ]);
  });

  it('checks the values before sending', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [CLASSROOMS]: classroomsPage,
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    typeInto(labelled(group(section(), 'Weekly maximum'), 'Hours'), '3');
    typeInto(
      labelled(group(section(), 'Required break between classes'), 'Minutes'),
      '300',
    );
    await click(buttonNamed(section(), 'Save limits and classrooms'));

    expect(api.calls(SETTINGS)).toHaveLength(0);
    expect(section().textContent).toContain(
      "The weekly maximum can't be below the daily maximum.",
    );
    expect(section().textContent).toContain('Enter between 0 and 240 minutes.');
    expect(document.activeElement).toBe(
      labelled(group(section(), 'Weekly maximum'), 'Hours'),
    );
  });

  it('shows server field errors on their fields', async () => {
    stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [CLASSROOMS]: classroomsPage,
      [SETTINGS]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Problem',
            status: 422,
            code: 'validation_error',
            request_id: 'req-1',
            errors: [
              {
                path: ['body', 'weekly_limit_minutes'],
                code: 'weekly_below_daily',
                message: 'x',
              },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    typeInto(labelled(group(section(), 'Daily maximum'), 'Hours'), '5');
    await click(buttonNamed(section(), 'Save limits and classrooms'));

    expect(section().textContent).toContain(
      "The weekly maximum can't be below the daily maximum.",
    );
  });

  it('version_conflict offers Reload', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, AVAILABILITY, {
      [CLASSROOMS]: classroomsPage,
      [SETTINGS]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    typeInto(labelled(group(section(), 'Daily maximum'), 'Hours'), '5');

    await click(buttonNamed(section(), 'Save limits and classrooms'));
    const before = api.calls(AVAILABILITY_ROUTE).length;
    await click(buttonNamed(section(), 'Reload'));

    expect(api.calls(AVAILABILITY_ROUTE).length).toBe(before + 1);
  });

  it('a Coordinator sees the values as text and loads no classrooms', async () => {
    const api = stub(COORDINATOR, TUTOR_READ_ONLY, AVAILABILITY, {
      [CLASSROOMS]: classroomsPage,
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const text = section().textContent ?? '';
    expect(text).toContain('Daily maximum4 h');
    expect(text).toContain('Weekly maximum20 h');
    expect(text).toContain('Required break between classes15 min');
    expect(text).toContain('Aula Vieja (inactive)');
    expect(text).toContain('Limits only produce warnings when scheduling');
    expect(buttonTexts(section())).toEqual([]);
    expect(api.calls(CLASSROOMS)).toHaveLength(0);
  });

  it('shows "No limit" and "No break" when none is set', async () => {
    stub(COORDINATOR, TUTOR_READ_ONLY, NO_AVAILABILITY);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const text = section().textContent ?? '';
    expect(text).toContain('Daily maximumNo limit');
    expect(text).toContain('Required break between classesNo break');
    expect(text).toContain('No preferred classrooms.');
  });

  it('shows the strings in Spanish', async () => {
    applyLanguage('es');
    stub(COORDINATOR, TUTOR_READ_ONLY, AVAILABILITY);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const text = section().textContent ?? '';
    expect(text).toContain('Límites de enseñanza');
    expect(text).toContain('Aula Vieja (inactiva)');
  });
});

describe('the settings rules (#197)', () => {
  it('round-trips the stored values without changes', () => {
    expect(settingsBody(settingsValues(AVAILABILITY), AVAILABILITY)).toBeNull();
    expect(
      settingsBody(settingsValues(NO_AVAILABILITY), NO_AVAILABILITY),
    ).toBeNull();
  });

  it.each([
    [{ hours: '24', minutes: '1' }, 'daily'],
    [{ hours: '0', minutes: '14' }, 'daily'],
    [{ hours: '', minutes: '' }, 'daily'],
    [{ hours: '1', minutes: '60' }, 'daily'],
    [{ hours: '1.5', minutes: '' }, 'daily'],
  ])('rejects the daily maximum %j', (daily, field) => {
    const values = settingsValues(NO_AVAILABILITY);
    expect(
      validateSettings({ ...values, daily: { none: false, ...daily } }),
    ).toHaveProperty(field);
  });

  it('accepts the bounds', () => {
    const values = settingsValues(NO_AVAILABILITY);
    expect(
      validateSettings({
        ...values,
        daily: { none: false, hours: '24', minutes: '0' },
        weekly: { none: false, hours: '168', minutes: '' },
        breakNone: false,
        breakMinutes: '240',
      }),
    ).toEqual({});
    expect(
      validateSettings({
        ...values,
        weekly: { none: false, hours: '168', minutes: '1' },
        breakNone: false,
        breakMinutes: '241',
        rooms: Array.from({ length: 11 }, (_, index) => String(index)),
      }),
    ).toEqual({
      weekly: 'tutors.availability.settings.validation.weeklyRange',
      break: 'tutors.availability.settings.validation.breakRange',
      rooms: 'tutors.availability.settings.validation.tooManyRooms',
    });
  });
});
