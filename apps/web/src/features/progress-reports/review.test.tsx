import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
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
import type { ProgressReport } from './api';
import {
  ADMIN_TUTOR,
  APPROVED,
  APPROVED_REVIEW,
  CHANGES_REQUESTED,
  COORDINATOR,
  GUARDIAN,
  OWN_SUBMITTED_REVIEWER,
  OWNER_TUTOR,
  REPORT_ID,
  REVIEW,
  SCRIPT,
  SUBMITTED,
} from './fixtures';

// #315 E2-E4, C8, H1 (review): the actions strictly by the flags, each
// dialog's body (approve {version}; request changes / retract {version,
// reason}, the reason required and trimmed), the retract warning only for
// an approved report, self-review (an Administrator author: disabled with
// the reason; an Owner author: the actions), every E4 row with its focus,
// the cache and query invalidation, focus return (Escape and Cancel to the
// trigger; the heading after a decision) and the history.

const DETAIL = `GET /api/v1/progress-reports/${REPORT_ID}`;
const APPROVE = `POST /api/v1/progress-reports/${REPORT_ID}/approve`;
const CHANGES = `POST /api/v1/progress-reports/${REPORT_ID}/request-changes`;
const RETRACT = `POST /api/v1/progress-reports/${REPORT_ID}/retract`;
const PAGE = `/progress-reports/${REPORT_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const focused = () => document.activeElement;
const heading = () => main().querySelector('h1');
const button = (name: string) => buttonNamed(main(), name);
const buttons = () =>
  [...main().querySelectorAll('button')].map((b) => b.textContent);
const statusText = () => main().querySelector('[role="status"]')?.textContent;

async function open(
  report: ProgressReport,
  routes: Routes = {},
  as: SessionUserBody = COORDINATOR,
) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [DETAIL]: () => jsonResponse(200, report),
    ...routes,
  });
  app = await renderApp(PAGE);
  await settleUntil(() => text().includes('José'));
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

describe('the review page (E2, C8)', () => {
  it('the narrative, the frozen facts, the history, "By"', async () => {
    await open({
      ...REVIEW,
      changes_requested: CHANGES_REQUESTED.changes_requested,
      submitted_at: '2026-10-25T18:00:00Z',
    });
    expect(text()).toContain('By Carmen Ruiz');
    expect(text()).toContain('Buen avance en física.');
    expect(text()).toContain('Facts as submitted on');
    expect(text()).not.toContain('Facts preview');
    const history = [...main().querySelectorAll('ol li')].map(
      (li) => li.textContent,
    );
    expect(history).toHaveLength(2);
    expect(history[0]).toContain(
      'Changes requested by Laura Méndez on Oct 25, 2026',
    );
    expect(history[0]).toContain('Añade los próximos pasos.');
    expect(history[1]).toContain('Submitted Oct 25, 2026');
    expect(main().querySelector('textarea')).toBeNull();
  });

  it('plain text everywhere: narrative and reasons', async () => {
    await open({
      ...APPROVED_REVIEW,
      summary: SCRIPT,
      changes_requested: {
        ...CHANGES_REQUESTED.changes_requested!,
        reason: SCRIPT,
      },
    });
    expect(text()).toContain(SCRIPT);
    expect(main().querySelector('script')).toBeNull();
  });
});

describe('actions strictly by the flags (E2)', () => {
  it('a submitted report with every flag: the three actions', async () => {
    await open(REVIEW);
    expect(buttons()).toEqual(
      expect.arrayContaining(['Approve', 'Request changes', 'Retract']),
    );
  });

  it('an approved report: Retract only', async () => {
    await open(APPROVED_REVIEW);
    expect(buttons()).toContain('Retract');
    expect(buttons()).not.toContain('Approve');
    expect(buttons()).not.toContain('Request changes');
  });

  it('a submitted report whose flags are all false: nothing', async () => {
    await open({
      ...REVIEW,
      can_approve: false,
      can_request_changes: false,
      can_retract: false,
    });
    expect(buttons()).not.toContain('Approve');
    expect(buttons()).not.toContain('Request changes');
    expect(buttons()).not.toContain('Retract');
  });

  it('flags win over the status (request changes only)', async () => {
    await open({ ...REVIEW, can_approve: false, can_retract: false });
    expect(buttons()).toContain('Request changes');
    expect(buttons()).not.toContain('Approve');
  });

  it('a guardian is sent to the family page, with no working request', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(GUARDIAN),
      'GET /api/v1/schedule/family-students': () =>
        jsonResponse(200, { items: [] }),
    });
    app = await renderApp(PAGE);
    await settleUntil(
      () => app!.location() === `/my-progress-reports/${REPORT_ID}`,
    );
    expect(api.calls(DETAIL)).toHaveLength(0);
  });
});

describe('approve (E2)', () => {
  it('sends the version read; the cache, the lists and the inbox refresh', async () => {
    await open(REVIEW, {
      [APPROVE]: () => jsonResponse(200, { ...APPROVED_REVIEW, version: 3 }),
    });
    const invalidate = vi.spyOn(app!.queryClient, 'invalidateQueries');
    const detailCalls = api.calls(DETAIL).length;
    await click(button('Approve'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Approve this report?');
    expect(dialog.textContent).toContain(
      'José Ñúñez Pérez and guardians with access will see it right away, exactly as shown here, including the facts.',
    );
    await click(buttonNamed(dialog, 'Approve'));
    expect(api.calls(APPROVE)[0]!.body).toEqual({ version: 2 });
    await settleUntil(() => focused() === heading());
    expect(statusText()).toBe('Report approved.');
    expect(text()).toContain('Approved');
    // The returned view replaced the cache (no refetch of R3).
    expect(api.calls(DETAIL).length).toBe(detailCalls);
    const keys = invalidate.mock.calls.map((call) =>
      JSON.stringify(call[0]?.queryKey),
    );
    expect(keys).toContain(JSON.stringify(['progress-reports', 'working']));
    expect(keys).toContain(JSON.stringify(['approval-requests']));
  });

  it('Escape and Cancel return focus to Approve', async () => {
    await open(REVIEW);
    await click(button('Approve'));
    await pressEscape();
    await settleUntil(() => focused() === button('Approve'));
    await click(button('Approve'));
    await click(buttonNamed(openDialog()!, 'Cancel'));
    await settleUntil(() => focused() === button('Approve'));
    expect(api.calls(APPROVE)).toHaveLength(0);
  });

  it('after a Reload, the version sent is the refetched one', async () => {
    let version = 2;
    await open(REVIEW, {
      [DETAIL]: () => jsonResponse(200, { ...REVIEW, version }),
      [APPROVE]: [
        () => problemWith(409, 'version_conflict'),
        () => jsonResponse(200, { ...APPROVED_REVIEW, version: 9 }),
      ],
    });
    await click(button('Approve'));
    await click(buttonNamed(openDialog()!, 'Approve'));
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.textContent?.includes(
        'This report changed since you opened it. Reload to review the latest version.',
      ),
    );
    version = 5;
    await click(buttonNamed(focused() as HTMLElement, 'Reload'));
    await settleUntil(() => focused() === heading());
    await click(button('Approve'));
    await click(buttonNamed(openDialog()!, 'Approve'));
    expect(api.calls(APPROVE)[1]!.body).toEqual({ version: 5 });
  });
});

describe('request changes (E2)', () => {
  it('the reason is required (nothing sent, focus stays) and trimmed', async () => {
    await open(REVIEW, {
      [CHANGES]: () =>
        jsonResponse(200, {
          ...CHANGES_REQUESTED,
          is_own: false,
          can_edit: false,
          can_submit: false,
          can_delete: false,
        }),
    });
    await click(button('Request changes'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'The reason goes to Carmen Ruiz only. Families never see it.',
    );
    const reason = labelled<HTMLTextAreaElement>(dialog, 'Reason');
    await click(buttonNamed(dialog, 'Request changes'));
    expect(api.calls(CHANGES)).toHaveLength(0);
    expect(focused()).toBe(reason);
    expect(reason.getAttribute('aria-invalid')).toBe('true');
    setFieldValue(reason, '  Añade ejemplos.  ');
    await click(buttonNamed(dialog, 'Request changes'));
    expect(api.calls(CHANGES)[0]!.body).toEqual({
      version: 2,
      reason: 'Añade ejemplos.',
    });
    await settleUntil(() => focused() === heading());
    expect(statusText()).toBe('Changes requested.');
  });

  it("the server's reason errors land on Reason", async () => {
    await open(REVIEW, {
      [CHANGES]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'reason'], code: 'too_long' }],
        }),
    });
    await click(button('Request changes'));
    const dialog = openDialog()!;
    const reason = labelled<HTMLTextAreaElement>(dialog, 'Reason');
    setFieldValue(reason, 'Motivo');
    await click(buttonNamed(dialog, 'Request changes'));
    await settleUntil(() => focused() === reason);
    expect(openDialog()).not.toBeNull();
    expect(dialog.textContent).toContain('This text is too long.');
  });

  it('Escape returns focus to Request changes', async () => {
    await open(REVIEW);
    await click(button('Request changes'));
    await pressEscape();
    await settleUntil(() => focused() === button('Request changes'));
  });

  it('progress_report_author_not_current: the E4 text, focused', async () => {
    await open(REVIEW, {
      [CHANGES]: () => problemWith(409, 'progress_report_author_not_current'),
    });
    await click(button('Request changes'));
    const dialog = openDialog()!;
    setFieldValue(labelled<HTMLTextAreaElement>(dialog, 'Reason'), 'Motivo');
    await click(buttonNamed(dialog, 'Request changes'));
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.textContent?.includes(
        'Carmen Ruiz no longer teaches José Ñúñez Pérez. Approve or retract the report instead.',
      ),
    );
    expect(openDialog()).toBeNull();
  });
});

describe('retract (E2)', () => {
  it('a submitted report: no family warning', async () => {
    await open(REVIEW);
    await click(button('Retract'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Retracting is final. Carmen Ruiz is notified with your reason.',
    );
    expect(dialog.textContent).not.toContain('will no longer see');
  });

  it('an approved report: the warning; the body; Cancel returns focus', async () => {
    await open(APPROVED_REVIEW, {
      [RETRACT]: () =>
        jsonResponse(200, {
          ...APPROVED_REVIEW,
          status: 'retracted',
          version: 5,
          can_retract: false,
          retracted: {
            at: '2026-10-27T15:00:00Z',
            by: { display_name: 'Laura Méndez' },
            reason: 'Datos erróneos',
          },
        }),
    });
    await click(button('Retract'));
    let dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'José Ñúñez Pérez and guardians will no longer see this report.',
    );
    await click(buttonNamed(dialog, 'Cancel'));
    await settleUntil(() => focused() === button('Retract'));
    await click(button('Retract'));
    dialog = openDialog()!;
    setFieldValue(
      labelled<HTMLTextAreaElement>(dialog, 'Reason'),
      ' Datos erróneos ',
    );
    await click(buttonNamed(dialog, 'Retract'));
    expect(api.calls(RETRACT)[0]!.body).toEqual({
      version: 4,
      reason: 'Datos erróneos',
    });
    await settleUntil(() => focused() === heading());
    expect(statusText()).toBe('Report retracted.');
    expect(text()).toContain('Retracted by Laura Méndez');
  });
});

describe('self-review (E3)', () => {
  it('an Administrator author: disabled actions with the reason', async () => {
    await open(OWN_SUBMITTED_REVIEWER, {}, ADMIN_TUTOR);
    const explanation =
      'You wrote this report, so another reviewer must review it.';
    for (const name of ['Approve', 'Request changes', 'Retract']) {
      const action = button(name);
      expect(action.getAttribute('aria-disabled')).toBe('true');
      const described = document.getElementById(
        action.getAttribute('aria-describedby') ?? '',
      );
      expect(described?.textContent).toBe(explanation);
      await click(action);
      expect(openDialog()).toBeNull();
    }
    expect(api.calls(APPROVE)).toHaveLength(0);
  });

  it('an Owner author gets the actions the flags allow', async () => {
    await open(
      { ...OWN_SUBMITTED_REVIEWER, can_approve: true, can_retract: true },
      {},
      OWNER_TUTOR,
    );
    expect(button('Approve').getAttribute('aria-disabled')).toBeNull();
    await click(button('Approve'));
    expect(openDialog()).not.toBeNull();
  });

  it('403 progress_report_self_review: the E3 text', async () => {
    await open(REVIEW, {
      [APPROVE]: () => problemWith(403, 'progress_report_self_review'),
    });
    await click(button('Approve'));
    await click(buttonNamed(openDialog()!, 'Approve'));
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.textContent?.includes(
        'You wrote this report, so another reviewer must review it.',
      ),
    );
  });
});

describe('refusals (E4)', () => {
  it.each([
    ['progress_report_not_submitted', 409, true],
    ['progress_report_closed', 409, true],
    ['permission_denied', 403, true],
  ])('%s: its message, focused', async (code, status, reload) => {
    await open(REVIEW, { [APPROVE]: () => problemWith(status, code) });
    await click(button('Approve'));
    await click(buttonNamed(openDialog()!, 'Approve'));
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.querySelector('[role="alert"]'),
    );
    const region = focused() as HTMLElement;
    expect(
      [...region.querySelectorAll('button')].some(
        (b) => b.textContent === 'Reload',
      ),
    ).toBe(reload);
    expect(region.textContent).not.toContain('Server detail');
  });

  it('a 404 shows the not-found page', async () => {
    await open(REVIEW, { [APPROVE]: () => problemWith(404, 'not_found') });
    await click(button('Approve'));
    await click(buttonNamed(openDialog()!, 'Approve'));
    await settleUntil(() => text().includes('Page not found'));
  });

  it('reason_required (top-level) lands on Reason', async () => {
    await open(REVIEW, {
      [RETRACT]: () => problemWith(422, 'reason_required'),
    });
    await click(button('Retract'));
    const dialog = openDialog()!;
    const reason = labelled<HTMLTextAreaElement>(dialog, 'Reason');
    setFieldValue(reason, 'x');
    await click(buttonNamed(dialog, 'Retract'));
    await settleUntil(() => focused() === reason);
    expect(dialog.textContent).toContain('Write a reason.');
  });
});

describe('the author (not a reviewer) reading a submitted report', () => {
  it('is read mode, without actions', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs({
        ...COORDINATOR,
        roles: ['tutor'],
        permissions: ['student.view'],
      }),
      [DETAIL]: () => jsonResponse(200, SUBMITTED),
    });
    app = await renderApp(PAGE);
    await settleUntil(() => text().includes('Waiting for review since'));
    expect(buttons()).not.toContain('Approve');
    expect(APPROVED.status).toBe('approved_for_family');
  });
});
