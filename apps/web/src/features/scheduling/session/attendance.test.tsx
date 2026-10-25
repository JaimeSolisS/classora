import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  ANA,
  BRUNO,
  NOT_STARTED,
  STAFF_VIEW,
} from '@/features/tutor-portal/attendance/fixtures';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { act } from 'react';
import { click, openDialog } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { CREATED } from '../create/fixtures';
import { CONFIRMED_SESSION, UNASSIGNED_SESSION } from './fixtures';

// #76 H7, G3: the staff session page's attendance section (who sees
// "Correct", the "not started" text, nothing for other states) and the
// correction dialog (validation, the 422 and 409 mapping, the success
// notice and reload).

const ID = CREATED.id;
const DETAIL = `GET /api/v1/class-sessions/${ID}`;
const ATTENDANCE = `GET /api/v1/class-sessions/${ID}/attendance`;
const CORRECT = `POST /api/v1/class-sessions/${ID}/attendance/corrections`;

const CORRECTOR: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'attendance.correct'],
};
const FINANCE: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'student.view'],
};

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';

async function open(
  user: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [DETAIL]: () => jsonResponse(200, CONFIRMED_SESSION),
    [`GET /api/v1/class-sessions/${ID}/offers`]: () =>
      jsonResponse(200, { items: [] }),
    [`GET /api/v1/class-sessions/${ID}/substitutions`]: () =>
      jsonResponse(200, { items: [] }),
    [ATTENDANCE]: () => jsonResponse(200, STAFF_VIEW),
    ...routes,
  });
  app = await renderApp(`/schedule/sessions/${ID}`, undefined);
}

function setValue(
  element: HTMLSelectElement | HTMLTextAreaElement,
  value: string,
): void {
  const prototype =
    element instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLTextAreaElement.prototype;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(
      new Event(element instanceof HTMLSelectElement ? 'change' : 'input', {
        bubbles: true,
      }),
    );
  });
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

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the attendance section (G3)', () => {
  it('lists the attendance with Correct buttons for attendance.correct', async () => {
    await open(CORRECTOR);
    await settleUntil(() => text().includes('Bruno Gómez'));

    const section = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Attendance',
    )!;
    const rows = [...section.querySelectorAll('tbody tr')].map((r) =>
      r.textContent?.trim(),
    );
    expect(rows[0]).toContain('Ana Díaz');
    expect(rows[0]).toContain('Present');
    expect(rows[1]).toContain('Absent');
    expect(rows[1]).toContain('Corrected');
    expect(rows[1]).toContain('Laura Coordinadora');
    expect(buttonNamed(section, 'Correct attendance of Ana Díaz')).toBeTruthy();
    expect(section.textContent).toContain('Correction history (1)');
  });

  it('is read-only without attendance.correct', async () => {
    await open(FINANCE);
    await settleUntil(() => text().includes('Bruno Gómez'));

    expect(
      [...main().querySelectorAll('button')].some((b) =>
        b.getAttribute('aria-label')?.startsWith('Correct attendance'),
      ),
    ).toBe(false);
  });

  it('says when attendance opens before the class starts', async () => {
    await open(CORRECTOR, {
      [ATTENDANCE]: () => jsonResponse(200, NOT_STARTED),
    });
    await settleUntil(() =>
      text().includes('Attendance opens when the class starts.'),
    );
  });

  it('is not shown for an unassigned session', async () => {
    await open(CORRECTOR, {
      [DETAIL]: () => jsonResponse(200, UNASSIGNED_SESSION),
    });
    await settleUntil(() => text().includes('Álgebra'));

    expect(api.calls(ATTENDANCE)).toHaveLength(0);
    expect(
      [...main().querySelectorAll('h2')].some(
        (h) => h.textContent === 'Attendance',
      ),
    ).toBe(false);
  });
});

describe('the correction dialog (G3)', () => {
  async function openDialogFor(
    name: string,
    routes: Record<string, Responder | Responder[]> = {},
  ): Promise<HTMLElement> {
    await open(CORRECTOR, routes);
    await settleUntil(() => text().includes('Bruno Gómez'));
    await click(buttonNamed(main(), `Correct attendance of ${name}`));
    const dialog = openDialog();
    expect(dialog).not.toBeNull();
    return dialog!;
  }

  it('disables the current status and requires a reason', async () => {
    const dialog = await openDialogFor('Ana Díaz');
    const select = dialog.querySelector('select')!;
    const present = [...select.options].find((o) => o.value === 'present')!;

    expect(present.disabled).toBe(true);
    expect(present.textContent).toBe('Present (current)');
    expect(select.value).toBe('late');
    await click(buttonNamed(dialog, 'Save correction'));

    expect(dialog.textContent).toContain('Give a reason.');
    expect(api.calls(CORRECT)).toHaveLength(0);
    expect(dialog.textContent).toContain('0/1000 characters');
  });

  it('sends the correction, closes and announces it', async () => {
    const dialog = await openDialogFor('Ana Díaz', {
      [CORRECT]: () => jsonResponse(201, STAFF_VIEW),
    });
    setValue(dialog.querySelector('select')!, 'absent');
    setValue(dialog.querySelector('textarea')!, '  Salió temprano  ');
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() => text().includes('Attendance corrected.'));

    expect(api.calls(CORRECT)[0]?.body).toEqual({
      version: STAFF_VIEW.version,
      student_id: ANA,
      status: 'absent',
      reason: 'Salió temprano',
    });
    expect(openDialog()).toBeNull();
    expect(api.calls(ATTENDANCE).length).toBeGreaterThan(1);
  });

  it('maps attendance_unchanged and reason_required to their fields', async () => {
    const unchanged = invalid(['body', 'status'], 'attendance_unchanged');
    const reason = invalid(['body', 'reason'], 'reason_required');
    const dialog = await openDialogFor('Bruno Gómez', {
      [CORRECT]: [() => unchanged, () => reason],
    });
    setValue(dialog.querySelector('textarea')!, 'Motivo');
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() =>
      dialog.textContent?.includes('The student already has this status.'),
    );
    expect(dialog.querySelector('select')?.getAttribute('aria-invalid')).toBe(
      'true',
    );

    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() => dialog.textContent?.includes('Give a reason.'));
    expect(api.calls(CORRECT)[0]?.body).toMatchObject({ student_id: BRUNO });
  });

  it('offers Reload after a version conflict', async () => {
    const dialog = await openDialogFor('Ana Díaz', {
      [CORRECT]: () => problem(409, 'version_conflict'),
    });
    setValue(dialog.querySelector('textarea')!, 'Motivo');
    await click(buttonNamed(dialog, 'Save correction'));
    await settleUntil(() =>
      dialog.textContent?.includes('Attendance changed elsewhere'),
    );

    const before = api.calls(ATTENDANCE).length;
    await click(buttonNamed(dialog, 'Reload'));
    await settleUntil(() => api.calls(ATTENDANCE).length > before);
    expect(openDialog()).toBeNull();
  });
});
