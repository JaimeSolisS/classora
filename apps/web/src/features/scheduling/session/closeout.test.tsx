import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import { STAFF_VIEW } from '@/features/tutor-portal/attendance/fixtures';
import type { SessionCloseoutView } from '@/features/tutor-portal/closeout/api';
import {
  APPROVED_RECORD,
  ATTENDANCE_CHANGED,
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
import {
  APPROVED_WITH_OLD_NOTE,
  CHANGES_REQUESTED_WITH_OLD_NOTE,
  CONFIRMED_SESSION,
  SUBMITTED_WITHOUT_NOTE,
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
    ['closeout_self_review', null],
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
