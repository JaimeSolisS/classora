import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import { STAFF_VIEW } from '@/features/tutor-portal/attendance/fixtures';
import type { SessionCloseoutView } from '@/features/tutor-portal/closeout/api';
import {
  APPROVED_RECORD,
  ATTENDANCE_CHANGED,
  ATTENDANCE_UNMARKED,
  CHANGES_REQUESTED_RECORD,
  CORRECTION_SUBMITTED,
  FINANCE_APPROVED,
  FINANCE_SUBMITTED,
  NO_CLOSEOUT,
  REVIEWABLE,
  STAFF_APPROVED,
  STAFF_CHANGES_REQUESTED,
  STAFF_SUBMITTED,
  WAITING,
} from '@/features/tutor-portal/closeout/fixtures';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { act } from 'react';
import { click, openDialog, pressEscape } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { CREATED } from '../create/fixtures';
import { i18n } from '@/i18n';
import {
  APPROVED_WITH_OLD_NOTE,
  CHANGES_REQUESTED_CAN_TAKE_OVER,
  CHANGES_REQUESTED_WITH_OLD_NOTE,
  CONFIRMED_SESSION,
  ON_BEHALF_AFTER_TAKEOVER,
  ON_BEHALF_FINANCE,
  ON_BEHALF_REVIEWER,
  SUBMITTED_WITHOUT_NOTE,
  WAITING_ALL_ABSENT,
  WAITING_CAN_SUBMIT_ON_BEHALF,
  WAITING_INCOMPLETE,
} from './fixtures';

// #77 H6, G3: the staff session page's Closeout section (the submitted
// closeout with the minutes difference as text, the summary or "Summary
// visible to academic reviewers", "No closeout recorded", "Waiting for the
// tutor's closeout"; nothing for a session that hasn't started). #78 I7:
// the review buttons by the API's flags, the three dialogs (validation,
// only valid bodies sent, the error mapping, focus), the statuses, the
// tutor's reason, the attendance marker and Re-approve, and the history
// with and without reasons.

const ID = CREATED.id;
const DETAIL = `GET /api/v1/class-sessions/${ID}`;
const CLOSEOUT = `GET /api/v1/class-sessions/${ID}/closeout`;

const STAFF: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'attendance.correct'],
};

const PAST = '2026-01-05T16:00:00Z';
const FUTURE = '2099-01-05T16:00:00Z';

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;

function section(): HTMLElement | undefined {
  return [...main().querySelectorAll('section')].find(
    (s) => s.querySelector('h2')?.textContent === 'Closeout',
  );
}

async function open(
  state: string,
  startsAt: string,
  closeout?: SessionCloseoutView,
  routes: Record<string, Responder | Responder[]> = {},
): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(STAFF),
    [DETAIL]: () =>
      jsonResponse(200, {
        ...CONFIRMED_SESSION,
        state,
        starts_at: startsAt,
        ends_at: new Date(Date.parse(startsAt) + 3_600_000).toISOString(),
      }),
    [`GET /api/v1/class-sessions/${ID}/offers`]: () =>
      jsonResponse(200, { items: [] }),
    [`GET /api/v1/class-sessions/${ID}/substitutions`]: () =>
      jsonResponse(200, { items: [] }),
    [`GET /api/v1/class-sessions/${ID}/attendance`]: () =>
      jsonResponse(200, STAFF_VIEW),
    ...(closeout ? { [CLOSEOUT]: () => jsonResponse(200, closeout) } : {}),
    ...routes,
  });
  app = await renderApp(`/schedule/sessions/${ID}`, undefined);
  await settleUntil(() => (main().textContent ?? '').includes('Attendance'));
  await settle();
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the closeout section (G3)', () => {
  it('shows a submitted closeout with the summary for reviewers', async () => {
    await open('completed', PAST, STAFF_SUBMITTED);
    await settleUntil(() => section()?.textContent?.includes('Submitted'));

    const content = section()!.textContent ?? '';
    expect(content).toContain('Submitted for review');
    expect(content).toContain('55 of 60 min');
    expect(content).toContain('5 min less than scheduled');
    expect(content).toContain('Carmen Ruiz');
    expect(content).toContain('Fracciones equivalentes');
    expect(content).toContain('Next steps');
    expect(section()!.querySelector('button')).toBeNull();
  });

  it('hides the summary for Finance', async () => {
    await open('completed', PAST, FINANCE_SUBMITTED);
    await settleUntil(() => section()?.textContent?.includes('Submitted'));

    const content = section()!.textContent ?? '';
    expect(content).toContain('55 of 60 min');
    expect(content).toContain('Summary visible to academic reviewers');
    expect(content).not.toContain('Fracciones');
  });

  it('says when no closeout was recorded', async () => {
    await open('completed', PAST, NO_CLOSEOUT);
    await settleUntil(() => section()?.textContent?.includes('No closeout'));
  });

  it("waits for the tutor's closeout on a started session", async () => {
    await open('confirmed', PAST, WAITING);
    await settleUntil(() =>
      section()?.textContent?.includes("Waiting for the tutor's closeout"),
    );
  });

  it('is not shown before the class starts', async () => {
    await open('confirmed', FUTURE);

    expect(section()).toBeUndefined();
    expect(api.calls(CLOSEOUT)).toHaveLength(0);
  });
});

// --- #78 review --------------------------------------------------------------

const APPROVE = `POST /api/v1/class-sessions/${ID}/closeout/approve`;
const CHANGES = `POST /api/v1/class-sessions/${ID}/closeout/request-changes`;
const CORRECT = `POST /api/v1/class-sessions/${ID}/closeout/corrections`;

function setValue(
  element: HTMLInputElement | HTMLTextAreaElement,
  value: string,
): void {
  const prototype =
    element instanceof HTMLInputElement
      ? HTMLInputElement.prototype
      : HTMLTextAreaElement.prototype;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function field(root: HTMLElement, label: string): HTMLTextAreaElement {
  const found = [...root.querySelectorAll('label')].find((l) =>
    (l.textContent ?? '').startsWith(label),
  );
  return root.querySelector<HTMLTextAreaElement>(
    `#${CSS.escape(found!.htmlFor)}`,
  )!;
}

function invalid(path: string[], code: string): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Unprocessable Content',
      status: 422,
      code: 'validation_error',
      request_id: 'req-422',
      errors: [{ path, code, message: 'x' }],
    }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function buttons(): string[] {
  return [...section()!.querySelectorAll('button')].map(
    (b) => b.textContent ?? '',
  );
}

async function ready(view: SessionCloseoutView, routes = {}): Promise<void> {
  await open('completed', PAST, view, routes);
  await settleUntil(() => section()?.textContent?.includes('Actual'));
}

describe('the review actions (#78 G1)', () => {
  it.each([
    [
      'a submitted closeout',
      REVIEWABLE,
      ['Approve', 'Request changes', 'Correct'],
    ],
    ['without the flags', STAFF_SUBMITTED, []],
    ['changes requested', STAFF_CHANGES_REQUESTED, []],
    ['approved', STAFF_APPROVED, ['Correct']],
    [
      'a correction request',
      CORRECTION_SUBMITTED,
      ['Approve', 'Request changes', 'Correct'],
    ],
    ['attendance changed', ATTENDANCE_CHANGED, ['Correct', 'Re-approve']],
    ['Finance', FINANCE_APPROVED, []],
  ])('shows the buttons the flags allow: %s', async (_name, view, expected) => {
    await ready(view);
    expect(buttons()).toEqual(expected);
  });

  it('approves with the closeout version after a confirmation', async () => {
    await ready(REVIEWABLE, {
      [APPROVE]: () => jsonResponse(200, STAFF_APPROVED),
    });
    const trigger = buttonNamed(section()!, 'Approve');
    await click(trigger);
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Actual: 55 of 60 min');

    await click(buttonNamed(dialog, 'Approve'));
    await settleUntil(() =>
      (main().textContent ?? '').includes('Closeout approved.'),
    );

    expect(api.calls(APPROVE)[0]?.body).toEqual({ version: 1 });
    expect(openDialog()).toBeNull();
    expect(api.calls(CLOSEOUT).length).toBeGreaterThan(1);
  });

  it.each([
    ['version_conflict', 'Reload'],
    ['closeout_not_submitted', 'Reload'],
    // #301b E3: a race with the submitter offers Reload.
    ['closeout_self_review', 'Reload'],
  ])('maps %s on approve', async (code, retry) => {
    await ready(REVIEWABLE, {
      [APPROVE]: () =>
        problem(code === 'closeout_self_review' ? 403 : 409, code),
    });
    await click(buttonNamed(section()!, 'Approve'));
    const dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Approve'));
    await settleUntil(() => dialog.querySelector('[role="alert"]') !== null);

    const reload = [...dialog.querySelectorAll('button')].some(
      (b) => b.textContent === 'Reload',
    );
    expect(reload).toBe(retry !== null);
    if (code === 'closeout_self_review') {
      expect(dialog.textContent).toContain(
        "You can't review your own closeout.",
      );
    }
    if (retry) {
      const before = api.calls(CLOSEOUT).length;
      await click(buttonNamed(dialog, 'Reload'));
      await settleUntil(() => api.calls(CLOSEOUT).length > before);
      expect(openDialog()).toBeNull();
    }
  });

  it('requires a reason to request changes and sends it trimmed', async () => {
    await ready(REVIEWABLE, {
      [CHANGES]: [
        () => invalid(['body', 'reason'], 'reason_too_long'),
        () => jsonResponse(200, STAFF_CHANGES_REQUESTED),
      ],
    });
    const trigger = buttonNamed(section()!, 'Request changes');
    await click(trigger);
    const dialog = openDialog()!;
    const reason = dialog.querySelector('textarea')!;
    expect(document.activeElement).toBe(reason);
    await click(buttonNamed(dialog, 'Send back to the tutor'));
    expect(dialog.textContent).toContain('Give a reason.');
    expect(reason.getAttribute('aria-invalid')).toBe('true');
    expect(api.calls(CHANGES)).toHaveLength(0);

    setValue(reason, '  Añade la tarea  ');
    await click(buttonNamed(dialog, 'Send back to the tutor'));
    await settleUntil(() =>
      dialog.textContent?.includes(
        'The reason can have at most 1000 characters.',
      ),
    );
    await click(buttonNamed(dialog, 'Send back to the tutor'));
    await settleUntil(() =>
      (main().textContent ?? '').includes('Closeout sent back to the tutor.'),
    );
    expect(api.calls(CHANGES).map((c) => c.body)).toEqual([
      { version: 1, reason: 'Añade la tarea' },
      { version: 1, reason: 'Añade la tarea' },
    ]);
  });

  it('returns focus to the trigger when a dialog is cancelled', async () => {
    await ready(REVIEWABLE);
    const trigger = buttonNamed(section()!, 'Request changes');
    await click(trigger);
    await pressEscape();
    await settle();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('the correction dialog (#78 G1)', () => {
  async function correctDialog(
    view = REVIEWABLE,
    routes = {},
    name = 'Correct',
  ): Promise<HTMLElement> {
    await ready(view, routes);
    await click(buttonNamed(section()!, name));
    return openDialog()!;
  }

  it('sends only the changed members with a reason', async () => {
    const dialog = await correctDialog(REVIEWABLE, {
      [CORRECT]: () => jsonResponse(200, REVIEWABLE),
    });
    const minutes = dialog.querySelector('input')!;
    expect(minutes.value).toBe('55');
    expect(field(dialog, 'Topics').value).toBe('Fracciones equivalentes');
    expect(dialog.textContent).not.toContain('Saving creates a new approval.');

    setValue(minutes, '50');
    setValue(field(dialog, 'Homework'), '   ');
    await click(buttonNamed(dialog, 'Save correction'));
    expect(dialog.textContent).toContain('Give a reason.');
    expect(api.calls(CORRECT)).toHaveLength(0);

    setValue(field(dialog, 'Reason for the correction'), ' Terminó antes ');
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() =>
      (main().textContent ?? '').includes('Closeout corrected.'),
    );

    expect(api.calls(CORRECT)[0]?.body).toEqual({
      version: 1,
      reason: 'Terminó antes',
      actual_minutes: 50,
      summary: { homework: null },
    });
  });

  it('sends nothing invalid or unchanged', async () => {
    const dialog = await correctDialog();
    setValue(field(dialog, 'Reason for the correction'), 'Motivo');

    await click(buttonNamed(dialog, 'Save correction'));
    expect(dialog.textContent).toContain('Nothing changed.');

    setValue(dialog.querySelector('input')!, '721');
    await click(buttonNamed(dialog, 'Save correction'));
    expect(dialog.textContent).toContain(
      'Enter a whole number of minutes from 0 to 720.',
    );

    setValue(dialog.querySelector('input')!, '50');
    setValue(field(dialog, 'Topics'), '');
    await click(buttonNamed(dialog, 'Save correction'));
    expect(dialog.textContent).toContain('Fill in this part of the summary.');
    expect(api.calls(CORRECT)).toHaveLength(0);
  });

  it('maps the server errors', async () => {
    const dialog = await correctDialog(REVIEWABLE, {
      [CORRECT]: [
        () => invalid(['body', 'summary', 'performance'], 'summary_required'),
        () => problem(422, 'closeout_unchanged'),
        () => problem(409, 'closeout_with_tutor'),
      ],
    });
    setValue(dialog.querySelector('input')!, '50');
    setValue(field(dialog, 'Reason for the correction'), 'Motivo');

    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(
      () =>
        field(dialog, 'Performance').getAttribute('aria-invalid') === 'true',
    );
    setValue(field(dialog, 'Performance'), 'Bien');
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() => dialog.textContent?.includes('Nothing changed.'));
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() =>
      dialog.textContent?.includes('the tutor is editing this closeout'),
    );
    expect(buttonNamed(dialog, 'Reload')).toBeTruthy();
  });

  it('says that correcting an approved closeout creates a new approval', async () => {
    const dialog = await correctDialog(STAFF_APPROVED);
    expect(dialog.textContent).toContain('Saving creates a new approval.');
  });

  it('re-approves changed attendance with the fields locked', async () => {
    const dialog = await correctDialog(
      ATTENDANCE_CHANGED,
      { [CORRECT]: () => jsonResponse(200, STAFF_APPROVED) },
      'Re-approve',
    );
    expect(dialog.textContent).toContain('Re-approve closeout');
    expect(dialog.querySelector('input')!.readOnly).toBe(true);
    expect(field(dialog, 'Topics').readOnly).toBe(true);
    expect(document.activeElement).toBe(
      field(dialog, 'Reason for the correction'),
    );

    setValue(
      field(dialog, 'Reason for the correction'),
      'Asistencia corregida',
    );
    await click(buttonNamed(dialog, 'Re-approve'));
    await settleUntil(() => api.calls(CORRECT).length === 1);
    expect(api.calls(CORRECT)[0]?.body).toEqual({
      version: APPROVED_RECORD.version,
      reason: 'Asistencia corregida',
    });
  });
});

describe('the review statuses and history (#78 G1)', () => {
  it('shows changes requested with the reason', async () => {
    await ready(STAFF_CHANGES_REQUESTED);
    const content = section()!.textContent ?? '';
    expect(content).toContain('Changes requested');
    expect(content).toContain(
      "Reviewer's reason: Añade la tarea asignada y los siguientes pasos",
    );
  });

  it('shows an approval with its history', async () => {
    await ready(STAFF_APPROVED);
    const content = section()!.textContent ?? '';
    expect(content).toMatch(/Approved · 50 min by Laura Méndez, /);
    expect(content).toMatch(
      /Approval 2 · 50 min · Laura Méndez, .*\(correction\) \(in force\)/,
    );
    expect(content).toMatch(
      /Approval 1 · 55 min · Laura Méndez, .*\(reviewed\)/,
    );
    expect(content).toContain('Changed: Actual teaching minutes');
    expect(content).toContain('Minutes: 55 → 50');
    expect(content).toContain('Reason: Terminó 5 minutos antes');
  });

  it("shows a correction request with the tutor's dated reason (AM3, #312)", async () => {
    await ready(CORRECTION_SUBMITTED);
    const content = section()!.textContent ?? '';
    expect(content).toContain('Correction submitted');
    // last_submitted_at 2026-11-12T15:00:00Z in the center zone (UTC-6).
    expect(content).toContain(
      "Tutor's reason (sent Nov 12, 2026, 9:00 AM): Salimos 5 minutos antes por un simulacro.",
    );
    expect(content).toContain('45 of 60 min');
  });

  it.each([
    ['approved', APPROVED_WITH_OLD_NOTE],
    ['changes requested', CHANGES_REQUESTED_WITH_OLD_NOTE],
    ['submitted without a note', SUBMITTED_WITHOUT_NOTE],
  ])("hides the tutor's reason when %s (#312)", async (_, view) => {
    await ready(view);
    const content = section()!.textContent ?? '';
    expect(content).not.toContain("Tutor's reason");
    expect(content).not.toContain('Salimos 5 minutos antes');
  });

  it('marks attendance changed since approval as text', async () => {
    await ready(ATTENDANCE_CHANGED);
    expect(section()!.textContent).toContain(
      'Attendance changed since approval.',
    );
  });

  it('hides the reasons from Finance', async () => {
    await ready(FINANCE_APPROVED);
    const content = section()!.textContent ?? '';
    expect(content).toContain('Reason visible to academic reviewers');
    expect(content).toContain('Minutes: 55 → 50');
    expect(content).not.toContain('Terminó');
  });
});

// --- #301b on behalf ---------------------------------------------------------

const ON_BEHALF_ROUTE = `/api/v1/class-sessions/${ID}/closeout/on-behalf`;
const SUBMIT_ON_BEHALF = `POST ${ON_BEHALF_ROUTE}`;
const TAKE_OVER = `PUT ${ON_BEHALF_ROUTE}`;
const ATTENDANCE = `GET /api/v1/class-sessions/${ID}/attendance`;
const STAFF_UNMARKED = { ...ATTENDANCE_UNMARKED, can_mark: false };

function problemWith(
  status: number,
  code: string,
  details: Record<string, unknown>,
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `req-${code}`,
      details,
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

const errorText = (code: string): string =>
  i18n.getFixedT('en', 'errors')(code as 'generic');

async function waiting(
  view: SessionCloseoutView = WAITING_CAN_SUBMIT_ON_BEHALF,
  routes: Record<string, Responder | Responder[]> = {},
): Promise<void> {
  await open('confirmed', PAST, view, routes);
  await settleUntil(() => section()?.textContent?.includes('Waiting'));
}

async function onBehalfDialog(
  view: SessionCloseoutView = WAITING_CAN_SUBMIT_ON_BEHALF,
  routes: Record<string, Responder | Responder[]> = {},
): Promise<HTMLElement> {
  await waiting(view, routes);
  await click(buttonNamed(section()!, 'Close out on behalf of Carmen Ruiz'));
  return openDialog()!;
}

function fillRequired(dialog: HTMLElement, reason = '  Tutor left  '): void {
  setValue(field(dialog, 'Topics'), ' Fracciones ');
  setValue(field(dialog, 'Performance'), 'Bien');
  setValue(field(dialog, 'Next steps'), 'Sumas');
  setValue(
    field(dialog, "Why are you closing this out on the tutor's behalf?"),
    reason,
  );
}

function submitButton(dialog: HTMLElement): HTMLElement {
  return buttonNamed(dialog, 'Submit closeout');
}

describe('close out on behalf (#301b C)', () => {
  it('shows the attendance count and the button only with the flag (C1)', async () => {
    await waiting(WAITING);
    expect(section()!.textContent).toContain('Attendance: 2 of 2 marked');
    expect(buttons()).toEqual([]);
    app!.unmount();
    app = undefined;

    await waiting();
    expect(buttons()).toEqual(['Close out on behalf of Carmen Ruiz']);
  });

  it('prefills the scheduled minutes, marks the required fields and counts the reason (C2)', async () => {
    const dialog = await onBehalfDialog();
    expect(dialog.querySelector('h2')?.textContent).toBe(
      'Close out on behalf of Carmen Ruiz',
    );
    expect(dialog.querySelector('input')!.value).toBe('60');
    expect(document.activeElement).toBe(dialog.querySelector('input'));
    expect(field(dialog, 'Topics').getAttribute('aria-required')).toBe('true');
    expect(field(dialog, 'Homework').getAttribute('aria-required')).toBe(
      'false',
    );
    expect(dialog.textContent).toContain('2 of 2 marked.');
    expect(dialog.textContent).toContain('0/1000 characters');
    setValue(
      field(dialog, "Why are you closing this out on the tutor's behalf?"),
      ' abc ',
    );
    expect(dialog.textContent).toContain('3/1000 characters');

    await click(submitButton(dialog));
    expect(dialog.textContent).toContain('Fill in this part of the summary.');
    expect(api.calls(SUBMIT_ON_BEHALF)).toHaveLength(0);
  });

  it('makes the summary optional when everyone was absent', async () => {
    const dialog = await onBehalfDialog(WAITING_ALL_ABSENT);
    expect(dialog.textContent).toContain(
      'Everyone was absent, so the summary is optional.',
    );
    expect(field(dialog, 'Topics').getAttribute('aria-required')).toBe('false');
  });

  it('requires a reason', async () => {
    const dialog = await onBehalfDialog();
    fillRequired(dialog, '   ');
    await click(submitButton(dialog));
    expect(dialog.textContent).toContain('Give a reason.');
    expect(
      document.activeElement ===
        field(dialog, "Why are you closing this out on the tutor's behalf?"),
    ).toBe(true);
    expect(api.calls(SUBMIT_ON_BEHALF)).toHaveLength(0);
  });

  it('sends nothing while attendance is incomplete and names the unmarked students (C2)', async () => {
    const dialog = await onBehalfDialog(WAITING_INCOMPLETE, {
      [ATTENDANCE]: () => jsonResponse(200, STAFF_UNMARKED),
    });
    expect(dialog.textContent).toContain(
      '1 of 2 marked. Mark the rest in Attendance above: Bruno Gómez',
    );
    fillRequired(dialog);
    await click(submitButton(dialog));

    const alert = dialog.querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain('Bruno Gómez');
    expect(document.activeElement).toBe(alert);
    expect(api.calls(SUBMIT_ON_BEHALF)).toHaveLength(0);
  });

  it('sends the session version, the summary and the reason, then reloads (C3, C4)', async () => {
    let submitted = false;
    const dialog = await onBehalfDialog(WAITING_CAN_SUBMIT_ON_BEHALF, {
      [SUBMIT_ON_BEHALF]: () => {
        submitted = true;
        return jsonResponse(201, STAFF_SUBMITTED);
      },
      [CLOSEOUT]: () =>
        jsonResponse(
          200,
          submitted ? STAFF_SUBMITTED : WAITING_CAN_SUBMIT_ON_BEHALF,
        ),
    });
    setValue(dialog.querySelector('input')!, '55');
    fillRequired(dialog);
    const closeoutReads = api.calls(CLOSEOUT).length;
    const attendanceReads = api.calls(ATTENDANCE).length;
    await click(submitButton(dialog));
    await settleUntil(() =>
      (main().textContent ?? '').includes(
        'Closeout submitted on behalf of Carmen Ruiz.',
      ),
    );

    expect(api.calls(SUBMIT_ON_BEHALF).map((c) => c.body)).toEqual([
      {
        version: WAITING_CAN_SUBMIT_ON_BEHALF.version,
        actual_minutes: 55,
        summary: {
          topics: 'Fracciones',
          performance: 'Bien',
          next_steps: 'Sumas',
        },
        reason: 'Tutor left',
      },
    ]);
    expect(openDialog()).toBeNull();
    expect(api.calls(CLOSEOUT).length).toBeGreaterThan(closeoutReads);
    expect(api.calls(ATTENDANCE).length).toBeGreaterThan(attendanceReads);
    // The trigger is gone: focus lands on the page's status message (the
    // #78 decisions' behaviour), never on the body.
    await settleUntil(
      () =>
        document.activeElement?.getAttribute('role') === 'status' &&
        document.activeElement.textContent ===
          'Closeout submitted on behalf of Carmen Ruiz.',
    );
    expect(section()!.textContent).toContain('Submitted for review');
  });

  it.each([
    ['session_not_ended', 409, false],
    ['closeout_already_submitted', 409, true],
    ['session_not_closable', 409, true],
    ['version_conflict', 409, true],
    ['closeout_on_behalf_own_session', 403, false],
    ['permission_denied', 403, false],
  ])('maps %s (C5)', async (code, status, reload) => {
    const dialog = await onBehalfDialog(WAITING_CAN_SUBMIT_ON_BEHALF, {
      [SUBMIT_ON_BEHALF]: () => problem(status, code),
    });
    fillRequired(dialog);
    await click(submitButton(dialog));
    await settleUntil(() => dialog.querySelector('[role="alert"]') !== null);

    expect(dialog.textContent).toContain(errorText(code));
    const hasReload = [...dialog.querySelectorAll('button')].some(
      (b) => b.textContent === 'Reload',
    );
    expect(hasReload).toBe(reload);
    // Typed values are kept.
    expect(field(dialog, 'Topics').value).toBe(' Fracciones ');
    if (reload) {
      const before = api.calls(CLOSEOUT).length;
      await click(buttonNamed(dialog, 'Reload'));
      await settleUntil(() => api.calls(CLOSEOUT).length > before);
      expect(openDialog()).toBeNull();
    }
  });

  it('names the students of a 409 closeout_attendance_incomplete (C5)', async () => {
    const dialog = await onBehalfDialog(WAITING_CAN_SUBMIT_ON_BEHALF, {
      [ATTENDANCE]: () => jsonResponse(200, STAFF_UNMARKED),
      [SUBMIT_ON_BEHALF]: () =>
        problemWith(409, 'closeout_attendance_incomplete', {
          missing_student_ids: [
            STAFF_UNMARKED.items[1]!.student.id,
            '00000000-0000-4000-8000-00000000dead',
          ],
        }),
    });
    fillRequired(dialog);
    await click(submitButton(dialog));
    await settleUntil(() => dialog.querySelector('[role="alert"]') !== null);

    const alert = dialog.querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain('0 of 2 marked.');
    expect(alert.textContent).toContain('Bruno Gómez, 1 more student');
    await click(submitButton(dialog));
    expect(api.calls(SUBMIT_ON_BEHALF)).toHaveLength(1);
  });

  it('puts the 422 field errors on the fields and focuses the first (C5)', async () => {
    const dialog = await onBehalfDialog(WAITING_CAN_SUBMIT_ON_BEHALF, {
      [SUBMIT_ON_BEHALF]: [
        () => invalid(['body', 'summary', 'performance'], 'too_long'),
        () => invalid(['body', 'reason'], 'reason_too_long'),
        () => invalid(['body', 'actual_minutes'], 'less_than_equal'),
      ],
    });
    fillRequired(dialog);
    await click(submitButton(dialog));
    await settleUntil(
      () =>
        field(dialog, 'Performance').getAttribute('aria-invalid') === 'true',
    );
    await settleUntil(
      () => document.activeElement === field(dialog, 'Performance'),
    );
    expect(dialog.textContent).toContain('At most 2000 characters.');

    setValue(field(dialog, 'Performance'), 'Bien');
    await click(submitButton(dialog));
    await settleUntil(() =>
      dialog.textContent?.includes(
        'The reason can have at most 1000 characters.',
      ),
    );

    setValue(
      field(dialog, "Why are you closing this out on the tutor's behalf?"),
      'Otro motivo',
    );
    await click(submitButton(dialog));
    await settleUntil(
      () =>
        dialog.querySelector('input')!.getAttribute('aria-invalid') === 'true',
    );
    expect(dialog.textContent).toContain(
      'Enter a whole number of minutes from 0 to 720.',
    );
  });
});

// #301b QA D1: a control disabled while the send is pending can't take
// focus, so the move must wait until the fields are enabled again. Every
// focus() call is recorded with whether its element was disabled then. In
// a browser a 0 ms timer can run before React re-enables the fields; jsdom
// usually renders first, so the tests run timers at once to force that
// order (a focus moved by a timer would hit the disabled field).
function recordFocus(): { target: Element; disabled: boolean }[] {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const realSetTimeout = window.setTimeout;
  vi.spyOn(window, 'setTimeout').mockImplementation(((
    handler: TimerHandler,
    delay?: number,
    ...rest: unknown[]
  ) => {
    if (typeof handler === 'function' && !delay) {
      (handler as (...args: unknown[]) => void)(...rest);
      return 0;
    }
    return realSetTimeout(handler, delay, ...rest);
  }) as typeof window.setTimeout);
  const calls: { target: Element; disabled: boolean }[] = [];
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const original = HTMLElement.prototype.focus;
  vi.spyOn(HTMLElement.prototype, 'focus').mockImplementation(function (
    this: HTMLElement,
    options?: FocusOptions,
  ) {
    calls.push({
      target: this,
      disabled: (this as HTMLInputElement).disabled === true,
    });
    original.call(this, options);
  });
  return calls;
}

describe('focus after a server 422 (#301b QA D1)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    [['body', 'summary', 'performance'], 'too_long', 'Performance'],
    [['body', 'actual_minutes'], 'less_than_equal', null],
  ] as const)(
    'focuses %j once it is enabled (on behalf)',
    async (path, code, label) => {
      const dialog = await onBehalfDialog(WAITING_CAN_SUBMIT_ON_BEHALF, {
        [SUBMIT_ON_BEHALF]: () => invalid([...path], code),
      });
      fillRequired(dialog);
      const calls = recordFocus();
      await click(submitButton(dialog));
      const target = label
        ? field(dialog, label)
        : dialog.querySelector('input')!;
      await settleUntil(() => document.activeElement === target);
      const onTarget = calls.filter((call) => call.target === target);
      expect(onTarget.length).toBeGreaterThan(0);
      expect(onTarget.every((call) => !call.disabled)).toBe(true);
    },
  );

  it('focuses the first invalid field of a correction once it is enabled', async () => {
    await ready(REVIEWABLE, {
      [CORRECT]: () => invalid(['body', 'summary', 'homework'], 'too_long'),
    });
    await click(buttonNamed(section()!, 'Correct'));
    const dialog = openDialog()!;
    setValue(field(dialog, 'Homework'), 'Otra tarea');
    setValue(field(dialog, 'Reason for the correction'), 'Motivo');
    const calls = recordFocus();
    await click(buttonNamed(dialog, 'Save correction'));
    const target = field(dialog, 'Homework');
    await settleUntil(() => document.activeElement === target);
    const onTarget = calls.filter((call) => call.target === target);
    expect(onTarget.every((call) => !call.disabled)).toBe(true);
  });
});

describe('take over a stuck closeout (#301b D)', () => {
  async function takeoverDialog(routes = {}): Promise<HTMLElement> {
    await ready(CHANGES_REQUESTED_CAN_TAKE_OVER, routes);
    await click(buttonNamed(section()!, 'Take over and resubmit'));
    return openDialog()!;
  }

  it('shows the button only with the flag, next to the reason (D1)', async () => {
    await ready(STAFF_CHANGES_REQUESTED);
    expect(buttons()).toEqual([]);
    app!.unmount();
    app = undefined;

    await ready(CHANGES_REQUESTED_CAN_TAKE_OVER);
    expect(buttons()).toEqual(['Take over and resubmit']);
    expect(section()!.textContent).toContain("Reviewer's reason");
  });

  it('prefills the closeout and PUTs with the closeout version (D2, D3, D4)', async () => {
    const dialog = await takeoverDialog({
      [TAKE_OVER]: () => jsonResponse(200, ON_BEHALF_REVIEWER),
    });
    expect(dialog.querySelector('h2')?.textContent).toBe(
      'Take over and resubmit',
    );
    expect(dialog.textContent).toContain(
      "The closeout goes back to review as submitted on Carmen Ruiz's behalf.",
    );
    expect(dialog.querySelector('input')!.value).toBe('55');
    expect(field(dialog, 'Topics').value).toBe('Fracciones equivalentes');
    expect(
      field(dialog, "Why are you closing this out on the tutor's behalf?")
        .value,
    ).toBe('');

    setValue(
      field(dialog, "Why are you closing this out on the tutor's behalf?"),
      'El tutor ya no está',
    );
    await click(buttonNamed(dialog, 'Resubmit'));
    await settleUntil(() =>
      (main().textContent ?? '').includes(
        'Closeout taken over and sent back to review.',
      ),
    );

    const [call] = api.calls(TAKE_OVER);
    expect(call?.body).toEqual({
      version: CHANGES_REQUESTED_RECORD.version,
      actual_minutes: 55,
      summary: {
        topics: 'Fracciones equivalentes',
        performance: 'Participaron y resolvieron los ejercicios',
        homework: 'Ejercicios 1 a 5',
        next_steps: 'Suma de fracciones',
      },
      reason: 'El tutor ya no está',
    });
    // The closeout's version, never the session's.
    expect(CHANGES_REQUESTED_RECORD.version).not.toBe(
      CHANGES_REQUESTED_CAN_TAKE_OVER.version,
    );
    expect(api.calls(SUBMIT_ON_BEHALF)).toHaveLength(0);
  });

  it.each([
    ['closeout_not_with_tutor', 409],
    ['closeout_under_review', 409],
    ['version_conflict', 409],
    ['not_found', 404],
  ])('maps %s with Reload (D5)', async (code, status) => {
    const dialog = await takeoverDialog({
      [TAKE_OVER]: () => problem(status, code),
    });
    setValue(
      field(dialog, "Why are you closing this out on the tutor's behalf?"),
      'Motivo',
    );
    await click(buttonNamed(dialog, 'Resubmit'));
    await settleUntil(() => dialog.querySelector('[role="alert"]') !== null);
    expect(buttonNamed(dialog, 'Reload')).toBeTruthy();
  });
});

describe('the on-behalf markers (#301b E)', () => {
  it('names the tutor, the reviewer, the time and the reason (E1)', async () => {
    await ready(ON_BEHALF_REVIEWER);
    const content = section()!.textContent ?? '';
    // submitted_at 2026-11-10T16:00:00Z in the center zone (UTC-6).
    expect(content).toContain(
      'Submitted on behalf of Carmen Ruiz by Laura Méndez, Nov 10, 2026, 10:00 AM',
    );
    expect(content).toContain(
      'Reason: El tutor dejó el centro antes de enviar el cierre.',
    );
    expect(content).toContain('Submitted by');
  });

  it('hides the reason from Finance (E1)', async () => {
    await ready(ON_BEHALF_FINANCE);
    const content = section()!.textContent ?? '';
    expect(content).toContain('Submitted on behalf of Carmen Ruiz');
    expect(content).toContain('Reason visible to academic reviewers');
    expect(content).not.toContain('dejó el centro');
    expect(buttons()).toEqual([]);
  });

  it('shows Request changes only with its flag (E2)', async () => {
    await ready(ON_BEHALF_REVIEWER);
    expect(buttons()).toEqual(['Approve', 'Correct']);
  });

  it("hides the reviewer's old reason after a takeover (#301 QA note)", async () => {
    await ready(ON_BEHALF_AFTER_TAKEOVER);
    const content = section()!.textContent ?? '';
    expect(content).not.toContain("Reviewer's reason");
    expect(content).not.toContain('Añade la tarea asignada');
  });

  it('maps closeout_submitted_on_behalf on request changes with Reload (E3)', async () => {
    await ready(REVIEWABLE, {
      [CHANGES]: () => problem(409, 'closeout_submitted_on_behalf'),
    });
    await click(buttonNamed(section()!, 'Request changes'));
    const dialog = openDialog()!;
    setValue(dialog.querySelector('textarea')!, 'Motivo');
    await click(buttonNamed(dialog, 'Send back to the tutor'));
    await settleUntil(() => dialog.querySelector('[role="alert"]') !== null);
    expect(dialog.textContent).toContain(
      errorText('closeout_submitted_on_behalf'),
    );
    expect(buttonNamed(dialog, 'Reload')).toBeTruthy();
  });

  it('maps closeout_self_review on a correction with Reload (E3)', async () => {
    await ready(REVIEWABLE, {
      [CORRECT]: () => problem(403, 'closeout_self_review'),
    });
    await click(buttonNamed(section()!, 'Correct'));
    const dialog = openDialog()!;
    setValue(dialog.querySelector('input')!, '50');
    setValue(field(dialog, 'Reason for the correction'), 'Motivo');
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() => dialog.querySelector('[role="alert"]') !== null);
    expect(dialog.textContent).toContain(errorText('closeout_self_review'));
    expect(buttonNamed(dialog, 'Reload')).toBeTruthy();
  });
});
