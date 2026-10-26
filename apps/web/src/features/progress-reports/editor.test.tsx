import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { JOSE_LIMITED } from '@/features/students/fixtures';
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
  APPROVED,
  APPROVED_PAGE,
  CHANGES_REQUESTED,
  DRAFT,
  EMPTY_DRAFT,
  EMPTY_WORKING,
  FORMER_AUTHOR,
  REPORT_ID,
  RETRACTED,
  SCRIPT,
  STUDENT_ID,
  SUBMITTED,
  TUTOR,
} from './fixtures';

// #315 C, H1 (editor): counters and limits, Save (only the changed keys,
// "Draft saved." focused), Submit (a blank summary refused first with focus
// on Summary and no dialog; a dirty form saved first; the confirm body; the
// resubmit wording; Escape and Cancel back to the trigger; focus on the
// heading after), Delete (its dialog, the navigation and the notice
// focused), the changes-requested banner, every read-only state (the former
// author too), the preview vs the frozen facts and "Refresh facts", every
// C9 row with its focus, Reload after a conflict (asks first when
// something was typed, then the heading), the leave guard and plain text.

const DETAIL = `GET /api/v1/progress-reports/${REPORT_ID}`;
const PATCH = `PATCH /api/v1/progress-reports/${REPORT_ID}`;
const DELETE = `DELETE /api/v1/progress-reports/${REPORT_ID}`;
const SUBMIT = `POST /api/v1/progress-reports/${REPORT_ID}/submit`;
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const APPROVED_LIST = `GET /api/v1/students/${STUDENT_ID}/progress-reports`;
const WORKING = 'GET /api/v1/progress-reports';
const PAGE = `/progress-reports/${REPORT_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const focused = () => document.activeElement;
const heading = () => main().querySelector('h1');
const field = (label: string) => labelled<HTMLTextAreaElement>(main(), label);
const button = (name: string) => buttonNamed(main(), name);
const hasButton = (name: string) =>
  [...main().querySelectorAll('button')].some((b) => b.textContent === name);
const describedText = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent)
    .join(' ');

function stub(report: ProgressReport, routes: Routes = {}) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(TUTOR),
    [DETAIL]: () => jsonResponse(200, report),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [APPROVED_LIST]: () => jsonResponse(200, APPROVED_PAGE),
    [WORKING]: () => jsonResponse(200, EMPTY_WORKING),
    ...routes,
  });
}

async function open(report: ProgressReport, routes: Routes = {}) {
  stub(report, routes);
  app = await renderApp(PAGE);
  await settleUntil(() => main().querySelector('h1')?.textContent);
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

describe('the header and the fields (C1, C2)', () => {
  it('shows the student, the period, the subject and the status', async () => {
    await open(DRAFT);
    expect(heading()!.textContent).toBe('Progress report');
    const student = [...main().querySelectorAll('a')].find(
      (anchor) => anchor.textContent === 'José Ñúñez Pérez',
    );
    expect(student?.getAttribute('href')).toBe(
      `/students/${STUDENT_ID}/progress-reports`,
    );
    expect(text()).toContain('On demand · Oct 1, 2026 – Oct 24, 2026');
    expect(text()).toContain('All subjects');
    expect(text()).toContain('Draft');
    // The author's own report: no "By".
    expect(text()).not.toContain('By Carmen Ruiz');
  });

  it('labelled textareas with counters; the summary help', async () => {
    await open(DRAFT);
    const summary = field('Summary');
    expect(describedText(summary)).toContain('Required to submit.');
    expect(describedText(summary)).toContain('22/4000 characters');
    expect(describedText(field('Strengths'))).toContain('0/2000 characters');
    expect(describedText(field('Areas for improvement'))).toContain('/2000');
    expect(describedText(field('Next steps'))).toContain('/2000');
  });

  it('text over the limit is refused on its field, nothing sent', async () => {
    await open(DRAFT);
    setFieldValue(field('Strengths'), 'a'.repeat(2001));
    await click(button('Save'));
    await settleUntil(() => focused() === field('Strengths'));
    expect(describedText(field('Strengths'))).toContain(
      'This text is too long.',
    );
    expect(api.calls(PATCH)).toHaveLength(0);
  });
});

describe('save (C2)', () => {
  it('sends the version and only the changed keys', async () => {
    await open(DRAFT, {
      [PATCH]: () =>
        jsonResponse(200, { ...DRAFT, version: 2, strengths: 'Constancia' }),
    });
    setFieldValue(field('Strengths'), '  Constancia  ');
    await click(button('Save'));
    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 1,
      strengths: 'Constancia',
    });
    await settleUntil(() => focused()?.textContent === 'Draft saved.');
    expect(focused()?.getAttribute('role')).toBe('status');
  });

  it('a cleared field is sent as ""', async () => {
    await open(DRAFT, {
      [PATCH]: () => jsonResponse(200, { ...DRAFT, version: 2, summary: null }),
    });
    setFieldValue(field('Summary'), '');
    await click(button('Save'));
    expect(api.calls(PATCH)[0]!.body).toEqual({ version: 1, summary: '' });
  });

  it('nothing changed: no request, the notice', async () => {
    await open(DRAFT);
    await click(button('Save'));
    expect(api.calls(PATCH)).toHaveLength(0);
    await settleUntil(() => focused()?.textContent === 'Draft saved.');
  });
});

describe('submit (C3)', () => {
  it('a blank summary: the field error, focus there, no dialog', async () => {
    await open(EMPTY_DRAFT);
    await click(button('Submit for review'));
    await settleUntil(() => focused() === field('Summary'));
    expect(describedText(field('Summary'))).toContain(
      'Write a summary before you submit.',
    );
    expect(openDialog()).toBeNull();
    expect(api.calls(SUBMIT)).toHaveLength(0);
  });

  it('a dirty form is saved first, then the dialog; confirm sends the new version', async () => {
    await open(DRAFT, {
      [PATCH]: () =>
        jsonResponse(200, { ...DRAFT, version: 2, next_steps: 'Practicar' }),
      [SUBMIT]: () => jsonResponse(200, { ...SUBMITTED, version: 3 }),
    });
    setFieldValue(field('Next steps'), 'Practicar');
    await click(button('Submit for review'));
    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 1,
      next_steps: 'Practicar',
    });
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Submit for review?');
    expect(dialog.textContent).toContain(
      'A reviewer must approve it before José Ñúñez Pérez and guardians with access see it.',
    );
    await click(buttonNamed(dialog, 'Submit for review'));
    expect(api.calls(SUBMIT)[0]!.body).toEqual({ version: 2 });
    await settleUntil(() => focused() === heading());
    expect(text()).toContain('Report submitted for review.');
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Report submitted for review.',
    );
    // Read-only now.
    expect(main().querySelector('textarea')).toBeNull();
    expect(text()).toContain('In review');
  });

  it('Escape and Cancel return focus to Submit', async () => {
    await open(DRAFT);
    const submit = button('Submit for review');
    await click(submit);
    expect(openDialog()).not.toBeNull();
    await pressEscape();
    await settleUntil(() => focused() === button('Submit for review'));
    await click(button('Submit for review'));
    await click(buttonNamed(openDialog()!, 'Cancel'));
    await settleUntil(() => focused() === button('Submit for review'));
    expect(api.calls(SUBMIT)).toHaveLength(0);
  });

  it('summary_required from the server lands on Summary', async () => {
    await open(DRAFT, { [SUBMIT]: () => problemWith(422, 'summary_required') });
    await click(button('Submit for review'));
    await click(buttonNamed(openDialog()!, 'Submit for review'));
    await settleUntil(() => focused() === field('Summary'));
    expect(describedText(field('Summary'))).toContain(
      'Write a summary before you submit.',
    );
  });
});

describe('changes requested (C5)', () => {
  it('the banner (not a live region) and Resubmit', async () => {
    await open(CHANGES_REQUESTED, {
      [SUBMIT]: () => jsonResponse(200, { ...SUBMITTED, version: 4 }),
    });
    expect(text()).toContain(
      'Changes requested by Laura Méndez on Oct 25, 2026',
    );
    expect(text()).toContain('Añade los próximos pasos.');
    const banner = [...main().querySelectorAll('div')].find((element) =>
      element.textContent?.startsWith('Changes requested by'),
    );
    expect(banner?.getAttribute('role')).toBeNull();
    expect(banner?.getAttribute('aria-live')).toBeNull();
    await click(button('Resubmit'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Resubmit for review?');
    await click(buttonNamed(dialog, 'Resubmit'));
    expect(api.calls(SUBMIT)[0]!.body).toEqual({ version: 3 });
  });
});

describe('delete (C4)', () => {
  it('confirms, sends the version, goes to the student with the notice focused', async () => {
    const urls: URL[] = [];
    await open(DRAFT, {
      [DELETE]: (request) => {
        urls.push(new URL(request.url));
        return new Response(null, { status: 204 });
      },
    });
    await click(button('Delete report'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Delete this report?');
    expect(dialog.textContent).toContain("This can't be undone.");
    expect(dialog.textContent).not.toContain('withdrawn');
    await click(buttonNamed(dialog, 'Delete'));
    expect(urls[0]!.searchParams.get('version')).toBe('1');
    await settleUntil(
      () => app!.location() === `/students/${STUDENT_ID}/progress-reports`,
    );
    await settleUntil(() => focused()?.textContent === 'Report deleted.');
  });

  it('a report sent back says the review request is withdrawn; Escape returns focus', async () => {
    await open(CHANGES_REQUESTED);
    await click(button('Delete report'));
    expect(openDialog()!.textContent).toContain(
      'The review request is withdrawn.',
    );
    await pressEscape();
    await settleUntil(() => focused() === button('Delete report'));
  });
});

describe('read-only states (C6)', () => {
  it('submitted: waiting since, the frozen facts, the history', async () => {
    await open(SUBMITTED);
    expect(text()).toContain('Waiting for review since');
    expect(text()).toContain('Facts as submitted on');
    expect(text()).not.toContain('Facts preview');
    expect(text()).toContain('History');
    expect(main().querySelector('textarea')).toBeNull();
    expect(hasButton('Save')).toBe(false);
  });

  it('approved: who and when, and the family view link', async () => {
    await open(APPROVED);
    expect(text()).toContain(
      'Approved by Laura Méndez on Oct 26, 2026. José Ñúñez Pérez and guardians with access can read it.',
    );
    const link = [...main().querySelectorAll('a')].find(
      (anchor) => anchor.textContent === 'See it as the family sees it',
    );
    expect(link?.getAttribute('href')).toBe(
      `/students/${STUDENT_ID}/progress-reports/${REPORT_ID}`,
    );
  });

  it('retracted: the reason and a link to start again', async () => {
    await open(RETRACTED);
    expect(text()).toContain(
      'Retracted by Laura Méndez on Oct 27, 2026. It is no longer visible to the family.',
    );
    expect(text()).toContain('Reason: Datos de otra alumna.');
    expect(
      [...main().querySelectorAll('a')].some(
        (anchor) => anchor.textContent === "See the student's progress reports",
      ),
    ).toBe(true);
  });

  it('the former author: read-only notice, no actions', async () => {
    await open(FORMER_AUTHOR);
    expect(text()).toContain(
      'You no longer teach José Ñúñez Pérez. This report is read-only.',
    );
    expect(main().querySelector('textarea')).toBeNull();
    expect(main().querySelectorAll('main button').length).toBe(0);
  });

  it('plain text: markup in the narrative shows literally', async () => {
    await open({ ...SUBMITTED, summary: SCRIPT, next_steps: SCRIPT });
    expect(text()).toContain(SCRIPT);
    expect(main().querySelector('script')).toBeNull();
  });
});

describe('facts (C7)', () => {
  it('edit mode: the preview, its note and Refresh facts', async () => {
    let calls = 0;
    await open(DRAFT, {
      [DETAIL]: () => {
        calls += 1;
        return jsonResponse(200, DRAFT);
      },
    });
    expect(text()).toContain('Facts preview');
    expect(text()).toContain(
      'They are compiled again and frozen when you submit.',
    );
    expect(text()).not.toContain('Facts as submitted');
    const before = calls;
    await click(button('Refresh facts'));
    await settleUntil(() => focused()?.textContent === 'Facts updated.');
    expect(calls).toBe(before + 1);
  });

  it('edit mode without a preview: no error', async () => {
    await open({ ...DRAFT, facts_preview: null });
    expect(text()).toContain("The preview isn't available right now.");
    expect(main().querySelector('[role="alert"]')).toBeNull();
  });

  it('a report sent back shows the preview, not the last frozen facts', async () => {
    await open(CHANGES_REQUESTED);
    expect(text()).toContain('Facts preview');
    expect(text()).not.toContain('Facts as submitted');
  });
});

describe('refusals (C9)', () => {
  it('too_long field errors land on their fields, focus on the first', async () => {
    await open(DRAFT, {
      [PATCH]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'areas_for_improvement'], code: 'too_long' },
            { path: ['body', 'next_steps'], code: 'too_long' },
          ],
        }),
    });
    setFieldValue(field('Next steps'), 'x');
    setFieldValue(field('Areas for improvement'), 'y');
    await click(button('Save'));
    await settleUntil(() => focused() === field('Areas for improvement'));
    expect(describedText(field('Next steps'))).toContain(
      'This text is too long.',
    );
  });

  it('version_conflict: the message; Reload asks first, then the heading', async () => {
    let version = 1;
    await open(DRAFT, {
      [PATCH]: () => problemWith(409, 'version_conflict'),
      [DETAIL]: () =>
        jsonResponse(200, { ...DRAFT, version, summary: `Versión ${version}` }),
    });
    setFieldValue(field('Strengths'), 'Algo');
    await click(button('Save'));
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.textContent?.includes(
        'This report changed elsewhere.',
      ),
    );
    const region = focused() as HTMLElement;
    // Typed changes: Reload asks first; Escape returns focus.
    await click(buttonNamed(region, 'Reload'));
    expect(openDialog()!.textContent).toContain('Discard your changes?');
    await pressEscape();
    await settleUntil(() => focused() === region);
    version = 7;
    await click(buttonNamed(region, 'Reload'));
    await click(buttonNamed(openDialog()!, 'Discard and reload'));
    await settleUntil(() => focused() === heading());
    expect(field('Summary').value).toBe('Versión 7');
  });

  it.each([
    ['progress_report_under_review', 409],
    ['progress_report_closed', 409],
    ['progress_report_author_only', 403],
    ['progress_report_read_only', 403],
  ])('%s: its message with Reload, focused', async (code, status) => {
    await open(DRAFT, { [PATCH]: () => problemWith(status, code) });
    setFieldValue(field('Strengths'), 'Algo');
    await click(button('Save'));
    await settleUntil(() =>
      (focused() as HTMLElement | null)?.querySelector('[role="alert"]'),
    );
    const region = focused() as HTMLElement;
    expect(buttonNamed(region, 'Reload')).toBeTruthy();
    expect(region.textContent).not.toContain('Server detail');
  });

  it('a 404 shows the not-found page', async () => {
    await open(DRAFT, { [PATCH]: () => problemWith(404, 'not_found') });
    setFieldValue(field('Strengths'), 'Algo');
    await click(button('Save'));
    await settleUntil(() => text().includes('Page not found'));
  });
});

describe('the leave guard (C2)', () => {
  it('asks before leaving with unsaved changes; Stay returns focus', async () => {
    await open(DRAFT);
    setFieldValue(field('Strengths'), 'Algo');
    const back = [...main().querySelectorAll('a')].find(
      (anchor) => anchor.textContent === 'Back to progress reports',
    )!;
    back.focus();
    await click(back);
    expect(openDialog()!.textContent).toContain('Leave without saving?');
    await click(buttonNamed(openDialog()!, 'Stay'));
    await settleUntil(() => focused() === back);
    expect(app!.location()).toBe(PAGE);
  });
});
