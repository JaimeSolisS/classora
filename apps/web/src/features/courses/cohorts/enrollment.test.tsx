import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { sessionOverride } from '@/features/scheduling/create/rules';
import { applyLanguage } from '@/i18n';
import { formatLocalDate } from '@/lib/time';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  click,
  fieldByLabel,
  openDialog,
  pressEscape,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import {
  COHORT,
  COHORT_CAPACITY_CONFLICT,
  COHORT_ID,
  EMPTY_ROSTER_COHORT,
  ENDED_ROSTER_COHORT,
  ENROLL_OVERLAP_CONFLICT,
  ENROLL_ROOM_CONFLICT,
  ENROLL_SOFT_OCCURRENCE,
  ENROLLED_ANA,
  ENROLLED_CARLA,
  FULL_COHORT,
  ROSTER_COHORT,
  STUDENT_ANA,
  STUDENT_CARLA,
} from './fixtures';
import {
  cohortCapacity,
  cohortWithdrawBody,
  editServerErrors,
  enrollBody,
  enrolledStudentIds,
  enrollmentControls,
  rosterParts,
  withdrawnShown,
} from './rules';

// #245 E4: the cohort's Students panel, the enroll and withdraw dialogs
// and the edit's below-enrolled message, in the unit project's foreign
// host zone (Pacific/Kiritimati, vite.config.ts) with the center in
// America/Mexico_City. The bodies (`override.codes`, never pairs; the
// withdrawal's version, a blank reason left out), each error's message,
// the "Full" and room-limit rules, the started/ended rules and the guards
// by permission. Waits are on conditions (vi.waitFor), never on a fixed
// number of turns.

const DETAIL = `GET /api/v1/cohorts/${COHORT_ID}`;
const PATCH = `PATCH /api/v1/cohorts/${COHORT_ID}`;
const ENROLL = `POST /api/v1/cohorts/${COHORT_ID}/enrollments`;
const WITHDRAW = `POST /api/v1/cohorts/${COHORT_ID}/enrollments/${ENROLLED_ANA.id}/withdraw`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const OWNER = user(
  ['owner'],
  [
    'course.view',
    'schedule.view',
    'schedule.manage',
    'schedule.assign',
    'schedule.override',
    'student.view',
  ],
);
/** An Administrator without the Owner's override delegation. */
const ADMIN = user(
  ['administrator'],
  [
    'course.view',
    'schedule.view',
    'schedule.manage',
    'schedule.assign',
    'student.view',
  ],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['course.view', 'schedule.view', 'schedule.assign'],
);
const FINANCE = user(['finance_staff'], ['course.view', 'schedule.view']);

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const WAIT = { timeout: 3000, interval: 20 };

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

const STUDENT_ROWS = [STUDENT_ANA, STUDENT_CARLA].map((student) => {
  const [given, family] = student.display_name.split(' ');
  return {
    id: student.id,
    given_names: given,
    family_names: family,
    is_active: true,
    is_minor: true,
    school_grade: null,
    education_level: null,
  };
});

function stub(
  as: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [DETAIL]: () => jsonResponse(200, ROSTER_COHORT),
    [`GET /api/v1/cohorts/${COHORT_ID}/sessions`]: () =>
      jsonResponse(200, { items: [] }),
    'GET /api/v1/tutors': page([]),
    'GET /api/v1/classrooms': page([]),
    'GET /api/v1/students': page(STUDENT_ROWS),
    ...routes,
  });
}

function conflictResponse(conflicts: unknown[]): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'scheduling_conflict',
      request_id: 'req-409',
      conflicts,
    }),
    { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function invalid(errors: { path: unknown[]; code: string }[]): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Unprocessable Content',
      status: 422,
      code: 'validation_error',
      request_id: 'req-422',
      errors: errors.map((e) => ({ ...e, message: 'x' })),
    }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

/** The cohort page, once the lazy route has rendered the detail's sections. */
async function open(path = `/courses/cohorts/${COHORT_ID}`) {
  const rendered = await renderApp(path);
  await vi.waitFor(() => {
    if (!rendered.container.querySelector('main section h2')) {
      throw new Error('not rendered yet');
    }
  }, WAIT);
  return rendered;
}

function students(): HTMLElement | null {
  const heading = [...main().querySelectorAll('h2')].find(
    (h) => h.textContent === 'Students',
  );
  return heading?.closest('section') ?? null;
}

function topDialog(): HTMLElement {
  const all = document.querySelectorAll<HTMLElement>('[role="dialog"]');
  return all[all.length - 1]!;
}

function typeArea(root: ParentNode, label: string, value: string) {
  const found = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === label,
  )!;
  const area = document.getElementById(found.htmlFor) as HTMLTextAreaElement;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** Opens "Enroll student", searches and picks `studentId`. */
async function enrollDialogWith(studentId: string): Promise<HTMLElement> {
  await click(buttonNamed(main(), 'Enroll student'));
  const dialog = openDialog()!;
  typeInto(fieldByLabel(dialog, 'Student'), 'a');
  const option = await vi.waitFor(() => {
    const found = dialog.querySelector<HTMLInputElement>(
      `input[value="${studentId}"]`,
    );
    if (!found) {
      throw new Error('no option yet');
    }
    return found;
  }, WAIT);
  await click(option);
  return dialog;
}

async function waitForText(root: () => ParentNode, value: string) {
  await vi.waitFor(() => {
    expect(root().textContent).toContain(value);
  }, WAIT);
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

// --- Pure rules ----------------------------------------------------------------

describe('the capacity line (A2)', () => {
  it('says "Full" from the effective capacity, and the room limit only below the maximum', () => {
    expect(cohortCapacity(ROSTER_COHORT)).toEqual({
      enrolled: 2,
      max: 12,
      roomLimit: null,
      full: false,
    });
    expect(cohortCapacity(FULL_COHORT)).toEqual({
      enrolled: 8,
      max: 12,
      roomLimit: 8,
      full: true,
    });
    // Full at exactly the maximum without a room; one seat short is not.
    expect(cohortCapacity({ ...COHORT, enrolled_count: 12 }).full).toBe(true);
    expect(cohortCapacity({ ...COHORT, enrolled_count: 11 }).full).toBe(false);
    expect(cohortCapacity({ ...FULL_COHORT, enrolled_count: 7 }).full).toBe(
      false,
    );
  });
});

describe('the controls (A1, B2, C1)', () => {
  it('enrolls only for schedule.manage before the end; the started help in progress', () => {
    expect(enrollmentControls({ status: 'upcoming' }, true)).toEqual({
      enroll: true,
      withdraw: true,
      startedHelp: false,
    });
    expect(enrollmentControls({ status: 'in_progress' }, true)).toEqual({
      enroll: true,
      withdraw: true,
      startedHelp: true,
    });
    expect(enrollmentControls({ status: 'ended' }, true)).toEqual({
      enroll: false,
      withdraw: true,
      startedHelp: false,
    });
    expect(enrollmentControls({ status: 'upcoming' }, false)).toEqual({
      enroll: false,
      withdraw: false,
      startedHelp: false,
    });
  });

  it('splits the roster and reads "Show withdrawn" from the URL', () => {
    const { active, withdrawn } = rosterParts(ROSTER_COHORT.roster!);
    expect(active.map((item) => item.student.display_name)).toEqual([
      'Ana López',
      'Bruno Díaz',
    ]);
    // Newest withdrawal first.
    expect(withdrawn.map((item) => item.student.display_name)).toEqual([
      'Carla Méndez',
      'Diego Ruiz',
    ]);
    expect([...enrolledStudentIds(ROSTER_COHORT.roster)].sort()).toEqual(
      active.map((item) => item.student.id).sort(),
    );
    expect(withdrawnShown(new URLSearchParams('withdrawn=1'))).toBe(true);
    expect(withdrawnShown(new URLSearchParams(''))).toBe(false);
    expect(withdrawnShown(new URLSearchParams('withdrawn=yes'))).toBe(false);
  });
});

describe('the bodies (B1, B3, C1) and the edit message (D1)', () => {
  it('enrolls with the student, and overrides with codes, never pairs', () => {
    expect(enrollBody(STUDENT_CARLA.id)).toEqual({
      student_id: STUDENT_CARLA.id,
    });
    const body = enrollBody(
      STUDENT_CARLA.id,
      sessionOverride(
        [
          COHORT_CAPACITY_CONFLICT,
          ENROLL_SOFT_OCCURRENCE,
          ENROLL_SOFT_OCCURRENCE,
        ],
        '  Hermana de Ana ',
      ),
    );
    expect(body).toEqual({
      student_id: STUDENT_CARLA.id,
      override: {
        codes: ['group_capacity_exceeded', 'outside_operating_hours'],
        reason: 'Hermana de Ana',
      },
    });
    expect(body.override).not.toHaveProperty('pairs');
  });

  it('withdraws with the version, the trimmed reason, and no blank reason', () => {
    expect(cohortWithdrawBody(3, '   ')).toEqual({ version: 3 });
    expect(cohortWithdrawBody(3, '')).toEqual({ version: 3 });
    expect(cohortWithdrawBody(3, ' Mudanza ')).toEqual({
      version: 3,
      reason: 'Mudanza',
    });
  });

  it('puts max_students_below_enrolled on the maximum', () => {
    expect(
      editServerErrors({
        fieldErrors: [
          {
            path: ['body', 'max_students'],
            code: 'max_students_below_enrolled',
            message: 'x',
          },
        ],
      }),
    ).toEqual({ max: 'courses.cohorts.enrollment.belowEnrolled' });
  });
});

// --- The panel (A) ---------------------------------------------------------------

describe('the Students panel (A)', () => {
  it('shows the capacity, the active rows by name and the withdrawn ones on demand', async () => {
    stub(OWNER);
    app = await open();
    const panel = students()!;
    expect(panel.getAttribute('aria-labelledby')).toBe(
      panel.querySelector('h2')!.id,
    );
    expect(panel.textContent).toContain('2 of 12 enrolled');
    expect(panel.textContent).not.toContain('Room limit');
    expect(panel.textContent).not.toContain('Full');
    const rows = [...panel.querySelectorAll('tbody tr')];
    expect(rows.map((row) => row.querySelector('td')!.textContent)).toEqual([
      'Ana López',
      'Bruno DíazInactive studentOverridden',
    ]);
    expect(
      panel.querySelector(`a[href="/students/${STUDENT_ANA.id}"]`)?.textContent,
    ).toBe('Ana López');
    // The center's local date (05:30Z is the evening before in Mexico City).
    expect(rows[0]!.textContent).toContain(
      formatLocalDate('2030-12-09', { language: 'en' }),
    );
    expect(rows[0]!.textContent).toContain('Demo Owner');
    expect(panel.textContent).not.toContain('Cambió de horario escolar.');

    const toggle = panel.querySelector<HTMLButtonElement>('[role="checkbox"]')!;
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(panel.querySelector(`label[for="${toggle.id}"]`)?.textContent).toBe(
      'Show withdrawn',
    );
    await click(toggle);
    await waitForText(() => students()!, 'Cambió de horario escolar.');
    expect(app.router.state.location.search).toBe('?withdrawn=1');
    expect(students()!.textContent).toContain('Withdrawn (2)');
    expect(students()!.textContent).toContain('No reason given');
    expect(students()!.textContent).toContain(
      formatLocalDate('2030-12-20', { language: 'en' }),
    );
    await click(
      students()!.querySelector<HTMLButtonElement>('[role="checkbox"]')!,
    );
    await vi.waitFor(() => {
      expect(students()!.textContent).not.toContain('Withdrawn (2)');
    }, WAIT);
    expect(app.router.state.location.search).toBe('');
  });

  it('opens with the withdrawn rows from ?withdrawn=1', async () => {
    stub(OWNER);
    app = await open(`/courses/cohorts/${COHORT_ID}?withdrawn=1`);
    expect(students()!.textContent).toContain('Cambió de horario escolar.');
    expect(
      students()!
        .querySelector('[role="checkbox"]')!
        .getAttribute('aria-checked'),
    ).toBe('true');
  });

  it('says "Full" and the room limit as text', async () => {
    stub(OWNER, { [DETAIL]: () => jsonResponse(200, FULL_COHORT) });
    app = await open();
    const panel = students()!;
    expect(panel.textContent).toContain('8 of 12 enrolled');
    expect(panel.textContent).toContain('Room limit: 8');
    expect(panel.textContent).toContain('Full');
  });

  it('has an empty state with "Enroll student"', async () => {
    stub(OWNER, { [DETAIL]: () => jsonResponse(200, EMPTY_ROSTER_COHORT) });
    app = await open();
    expect(students()!.textContent).toContain('No students enrolled yet.');
    expect(buttonNamed(students()!, 'Enroll student')).toBeTruthy();
  });

  it('is read-only for a Coordinator and Finance, and absent without a roster', async () => {
    for (const as of [COORDINATOR, FINANCE]) {
      stub(as);
      app = await open();
      const panel = students()!;
      expect(panel.textContent).toContain('2 of 12 enrolled');
      expect(panel.querySelectorAll('tbody tr')).toHaveLength(2);
      expect(() => buttonNamed(panel, 'Enroll student')).toThrow();
      expect(() => buttonNamed(panel, 'Withdraw Ana López')).toThrow();
      // No student.view: the names aren't links.
      expect(panel.querySelector('a[href^="/students/"]')).toBeNull();
      app.unmount();
    }

    stub(OWNER, { [DETAIL]: () => jsonResponse(200, COHORT) });
    app = await open();
    expect(students()).toBeNull();
  });

  it('has no "Enroll student" once ended, but still "Withdraw"', async () => {
    stub(OWNER, { [DETAIL]: () => jsonResponse(200, ENDED_ROSTER_COHORT) });
    app = await open();
    expect(() => buttonNamed(students()!, 'Enroll student')).toThrow();
    expect(buttonNamed(students()!, 'Withdraw Ana López')).toBeTruthy();
  });
});

// --- Enroll (B) ------------------------------------------------------------------

describe('the enroll dialog (B)', () => {
  it('enrolls a picked student, refreshes the roster and announces it', async () => {
    const api = stub(OWNER, {
      [DETAIL]: [
        () => jsonResponse(200, { ...ROSTER_COHORT, status: 'in_progress' }),
        () =>
          jsonResponse(200, {
            ...ROSTER_COHORT,
            status: 'in_progress',
            enrolled_count: 3,
            roster: [...ROSTER_COHORT.roster!, ENROLLED_CARLA],
          }),
      ],
      [ENROLL]: () => jsonResponse(201, ENROLLED_CARLA),
    });
    app = await open();
    const trigger = buttonNamed(main(), 'Enroll student');
    act(() => trigger.focus());
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    // B2: a started cohort says what the student joins.
    expect(dialog.textContent).toContain(
      "The student joins only the sessions that haven't started yet.",
    );
    // B1: the enrolled student can't be picked.
    const ana = dialog.querySelector<HTMLInputElement>(
      `input[value="${STUDENT_ANA.id}"]`,
    )!;
    expect(ana.disabled).toBe(true);
    expect(dialog.textContent).toContain('Ana López (already enrolled)');
    await click(buttonNamed(dialog, 'Enroll'));
    await vi.waitFor(() => expect(openDialog()).toBeNull(), WAIT);
    expect(api.calls(ENROLL).map((c) => c.body)).toEqual([
      { student_id: STUDENT_CARLA.id },
    ]);
    await waitForText(main, 'Enrolled Carla Méndez.');
    await waitForText(() => students()!, '3 of 12 enrolled');
    expect(api.calls(DETAIL).length).toBeGreaterThanOrEqual(2);
    expect(document.activeElement).toBe(buttonNamed(main(), 'Enroll student'));
  });

  it('has no started help before the first session', async () => {
    stub(OWNER);
    app = await open();
    await click(buttonNamed(main(), 'Enroll student'));
    expect(openDialog()!.textContent).not.toContain("haven't started yet");
  });

  it('overrides a soft-only 409 with codes and a reason', async () => {
    const api = stub(OWNER, {
      [ENROLL]: [
        () =>
          conflictResponse([ENROLL_SOFT_OCCURRENCE, COHORT_CAPACITY_CONFLICT]),
        () => jsonResponse(201, { ...ENROLLED_CARLA, overridden: true }),
      ],
    });
    app = await open();
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    await click(buttonNamed(dialog, 'Enroll'));
    const panel = await vi.waitFor(() => {
      const found = dialog.querySelector('[role="alert"]');
      if (!found) {
        throw new Error('no panel yet');
      }
      return found;
    }, WAIT);
    // The cohort-wide conflict first, then each local date.
    expect([...panel.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(
      [
        'Whole cohort',
        formatLocalDate('2031-01-13', { language: 'en', style: 'long' }),
      ],
    );
    expect(panel.textContent).toContain('Can be overriddenThe cohort is full.');
    await click(buttonNamed(dialog, 'Override and enroll'));
    typeArea(topDialog(), 'Reason', '  Hermana de Ana ');
    await click(buttonNamed(topDialog(), 'Override and enroll'));
    await vi.waitFor(() => expect(openDialog()).toBeNull(), WAIT);
    expect(api.calls(ENROLL).map((c) => c.body)).toEqual([
      { student_id: STUDENT_CARLA.id },
      {
        student_id: STUDENT_CARLA.id,
        override: {
          codes: ['outside_operating_hours', 'group_capacity_exceeded'],
          reason: 'Hermana de Ana',
        },
      },
    ]);
    await waitForText(main, 'Enrolled Carla Méndez.');
  });

  it('labels hard codes "Can\'t be overridden" with no override action', async () => {
    stub(OWNER, {
      [ENROLL]: () =>
        conflictResponse([
          COHORT_CAPACITY_CONFLICT,
          ENROLL_ROOM_CONFLICT,
          ENROLL_OVERLAP_CONFLICT,
        ]),
    });
    app = await open();
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    await click(buttonNamed(dialog, 'Enroll'));
    await waitForText(() => dialog, "Can't be overridden");
    const labels = [
      ...dialog.querySelectorAll('[role="alert"] li > span:first-child'),
    ].map((span) => span.textContent);
    expect(labels).toEqual([
      'Can be overridden',
      "Can't be overridden",
      "Can't be overridden",
    ]);
    expect(dialog.textContent).not.toContain('Not allowed');
    expect(dialog.textContent).toContain(
      'Fix the conflicts that aren’t allowed first.',
    );
    expect(() => buttonNamed(dialog, 'Override and enroll')).toThrow();
  });

  it('says soft codes need an override for an Administrator without it', async () => {
    stub(ADMIN, {
      [ENROLL]: () => conflictResponse([COHORT_CAPACITY_CONFLICT]),
    });
    app = await open();
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    await click(buttonNamed(dialog, 'Enroll'));
    await waitForText(() => dialog, 'Needs an override (Owner)');
    expect(dialog.textContent).not.toContain('Can be overridden');
    expect(() => buttonNamed(dialog, 'Override and enroll')).toThrow();
  });

  it('shows already_enrolled and refreshes the roster', async () => {
    const api = stub(OWNER, {
      [ENROLL]: () => problem(409, 'already_enrolled'),
    });
    app = await open();
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    const before = api.calls(DETAIL).length;
    await click(buttonNamed(dialog, 'Enroll'));
    await waitForText(() => dialog, 'This student is already enrolled.');
    await vi.waitFor(
      () => expect(api.calls(DETAIL).length).toBeGreaterThan(before),
      WAIT,
    );
    expect(openDialog()).not.toBeNull();
  });

  it('says the cohort has ended after a race, keeping the dialog open', async () => {
    const api = stub(OWNER, {
      [DETAIL]: [
        () => jsonResponse(200, ROSTER_COHORT),
        () => jsonResponse(200, { ...ROSTER_COHORT, status: 'ended' }),
      ],
      [ENROLL]: () => problem(409, 'cohort_ended'),
    });
    app = await open();
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    await click(buttonNamed(dialog, 'Enroll'));
    await waitForText(() => dialog, 'This cohort has ended');
    await vi.waitFor(
      () => expect(api.calls(DETAIL).length).toBeGreaterThanOrEqual(2),
      WAIT,
    );
    expect(openDialog()).not.toBeNull();
  });

  it('puts student_inactive on the picker and a 403 in an alert', async () => {
    stub(OWNER, {
      [ENROLL]: [
        () =>
          invalid([{ path: ['body', 'student_id'], code: 'student_inactive' }]),
        () => problem(403, 'permission_denied'),
      ],
    });
    app = await open();
    const dialog = await enrollDialogWith(STUDENT_CARLA.id);
    await click(buttonNamed(dialog, 'Enroll'));
    await waitForText(
      () => dialog,
      'This student is inactive. Choose another one.',
    );
    await click(buttonNamed(dialog, 'Enroll'));
    await vi.waitFor(() => {
      expect(dialog.querySelector('[role="alert"]')).not.toBeNull();
    }, WAIT);
    expect(dialog.textContent).not.toContain('This student is inactive.');
  });
});

// --- Withdraw (C) ----------------------------------------------------------------

describe('the withdraw dialog (C)', () => {
  it('says what happens, starts away from the destructive button and sends the version', async () => {
    const withdrawn = {
      ...ENROLLED_ANA,
      withdrawn_at: '2031-01-02T18:00:00Z',
      withdrawn_by: ENROLLED_ANA.enrolled_by,
      withdrawal_reason: 'Mudanza',
      version: 2,
    };
    const api = stub(OWNER, {
      [WITHDRAW]: [
        () => jsonResponse(200, { ...withdrawn, withdrawal_reason: null }),
        () => jsonResponse(200, withdrawn),
      ],
    });
    app = await open();
    const trigger = buttonNamed(main(), 'Withdraw Ana López');
    act(() => trigger.focus());
    await click(trigger);
    let dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Withdraw Ana López from Álgebra enero?',
    );
    expect(dialog.textContent).toContain(
      'Past and started sessions keep the student; future sessions drop them.',
    );
    expect(document.activeElement).not.toBe(buttonNamed(dialog, 'Withdraw'));
    expect(document.activeElement?.tagName).toBe('TEXTAREA');
    await pressEscape();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(trigger);

    await click(trigger);
    dialog = openDialog()!;
    typeArea(dialog, 'Reason (optional)', '   ');
    await click(buttonNamed(dialog, 'Withdraw'));
    await vi.waitFor(() => expect(openDialog()).toBeNull(), WAIT);
    await waitForText(main, 'Withdrew Ana López.');

    await click(buttonNamed(main(), 'Withdraw Ana López'));
    dialog = openDialog()!;
    typeArea(dialog, 'Reason (optional)', ' Mudanza ');
    await click(buttonNamed(dialog, 'Withdraw'));
    await vi.waitFor(() => expect(openDialog()).toBeNull(), WAIT);
    expect(api.calls(WITHDRAW).map((c) => c.body)).toEqual([
      { version: ENROLLED_ANA.version },
      { version: ENROLLED_ANA.version, reason: 'Mudanza' },
    ]);
  });

  it.each([
    [
      'version_conflict',
      409,
      'This enrollment changed. The list was reloaded.',
    ],
    [
      'enrollment_not_active',
      409,
      'This student was already withdrawn. The list is updated.',
    ],
    [
      'enrollment_not_found',
      404,
      'This enrollment no longer exists. The list was reloaded.',
    ],
  ] as const)(
    'closes on %s, reloads the roster and says so',
    async (code, status, message) => {
      const api = stub(OWNER, { [WITHDRAW]: () => problem(status, code) });
      app = await open();
      await click(buttonNamed(main(), 'Withdraw Ana López'));
      const before = api.calls(DETAIL).length;
      await click(buttonNamed(openDialog()!, 'Withdraw'));
      await vi.waitFor(() => expect(openDialog()).toBeNull(), WAIT);
      const notice = main().querySelector('[role="status"][tabindex="-1"]');
      expect(notice?.textContent).toBe(message);
      await vi.waitFor(
        () => expect(api.calls(DETAIL).length).toBeGreaterThan(before),
        WAIT,
      );
    },
  );

  it('puts reason_too_long on the reason', async () => {
    stub(OWNER, {
      [WITHDRAW]: () =>
        invalid([{ path: ['body', 'reason'], code: 'reason_too_long' }]),
    });
    app = await open();
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    const dialog = openDialog()!;
    typeArea(dialog, 'Reason (optional)', 'Mudanza');
    await click(buttonNamed(dialog, 'Withdraw'));
    await waitForText(
      () => dialog,
      'The reason can have at most 1000 characters.',
    );
    expect(document.activeElement?.tagName).toBe('TEXTAREA');
    expect(document.activeElement?.getAttribute('aria-invalid')).toBe('true');
  });
});

// --- Edit (D) ---------------------------------------------------------------------

describe('the edit dialog (D1)', () => {
  it('says how many students are enrolled when the maximum is too low', async () => {
    stub(OWNER, {
      [PATCH]: () =>
        invalid([
          {
            path: ['body', 'max_students'],
            code: 'max_students_below_enrolled',
          },
        ]),
    });
    app = await open();
    await click(buttonNamed(main(), 'Edit'));
    const dialog = openDialog()!;
    typeInto(fieldByLabel(dialog, 'Maximum students'), '1');
    await click(buttonNamed(dialog, 'Save'));
    await waitForText(() => dialog, 'At least 2 students are enrolled.');
    expect(
      fieldByLabel(dialog, 'Maximum students').getAttribute('aria-invalid'),
    ).toBe('true');
  });
});
