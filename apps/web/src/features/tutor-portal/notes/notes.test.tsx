import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { FamilyNotesList } from '@/features/my-schedule/FamilyNotesSection';
import { EntryDialog } from '@/features/my-schedule/EntryDialog';
import { tutorItem } from '@/features/my-schedule/fixtures';
import { NotesSection } from '@/features/scheduling/session/NotesSection';
import { NOTIFICATION_LINKS } from '@/features/notifications/messages';
import { ApiError } from '@/app/api';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  noContent,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import { alerts, click, openDialog, pressEscape } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { createElement } from 'react';
import { CREATED } from '@/features/scheduling/create/fixtures';
import { CONFIRMED_SESSION } from '@/features/scheduling/session/fixtures';
import { STAFF_VIEW } from '@/features/tutor-portal/attendance/fixtures';
import {
  ANA,
  BRUNO,
  EARLIER,
  FAMILY_NOTE,
  history,
  INTERNAL,
  NOTE_ID,
  READ_ONLY,
  SCRIPT,
  SESSION,
  SESSION_ID,
  SHARED,
  SHARED_ID,
  UNPUBLISHED,
  ZONE,
} from './fixtures';
import { bodyError, reasonError, showsNotFound, tutorNotesLink } from './rules';

// #79 I6: the tutor's notes page (composer validation, the badges, the
// actions by can_*, the share confirmation with the exact text, plain-text
// rendering, the error mapping), the My schedule links (tutor by state;
// the family section rendered, absent, failed with retry), the staff
// section's reason requirement and the notification links.

const PATH = `/my-schedule/sessions/${SESSION_ID}/notes`;
const ATTENDANCE = `GET /api/v1/class-sessions/${SESSION_ID}/attendance`;
const LIST = `GET /api/v1/class-sessions/${SESSION_ID}/notes`;
const CREATE = `POST /api/v1/class-sessions/${SESSION_ID}/notes`;
const HISTORY = `GET /api/v1/students/${ANA}/notes`;

const TUTOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['tutor'],
  permissions: ['student.view', 'tutor.view'],
};

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';

function setValue(element: HTMLTextAreaElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function composer(): HTMLTextAreaElement {
  const label = [...main().querySelectorAll('label')].find((l) =>
    l.textContent?.startsWith('New note about'),
  );
  const found = label ? document.getElementById(label.htmlFor) : null;
  if (!(found instanceof HTMLTextAreaElement)) {
    throw new Error('No composer');
  }
  return found;
}

async function open(
  notes = [INTERNAL, SHARED],
  earlier = history(EARLIER),
): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(TUTOR),
    [ATTENDANCE]: () => jsonResponse(200, SESSION),
    [LIST]: () => jsonResponse(200, { items: notes }),
    [HISTORY]: () => jsonResponse(200, earlier),
    [`GET /api/v1/students/${BRUNO}/notes`]: () => jsonResponse(200, history()),
  });
  app = await renderApp(PATH);
  await settleUntil(() => text().includes('Notes about Ana Díaz'));
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

describe('the rules', () => {
  it.each([
    ['', 'body_required'],
    ['   ', 'body_required'],
    ['x', undefined],
    ['x'.repeat(4000), undefined],
    [` ${'x'.repeat(4000)} `, undefined],
    ['x'.repeat(4001), 'too_long'],
  ])('the body %j is %s', (body, expected) => {
    expect(bodyError(body)).toBe(expected);
  });

  it.each([
    ['', 'reason_required'],
    ['  ', 'reason_required'],
    ['Dato médico', undefined],
    ['x'.repeat(501), 'reason_too_long'],
  ])('the reason %j is %s', (reason, expected) => {
    expect(reasonError(reason)).toBe(expected);
  });

  it.each([
    ['confirmed', true],
    ['completed', true],
    ['offered', false],
    ['cancelled', false],
    ['skipped_closure', false],
  ])('a tutor %s item offers Notes: %s', (state, expected) => {
    expect(tutorNotesLink({ state })).toBe(expected);
  });

  const error = (status: number, code: string) =>
    new ApiError({ status, code });

  it('maps the not-found codes, never version_conflict', () => {
    expect(showsNotFound(error(409, 'note_published'))).toBe(true);
    expect(showsNotFound(error(403, 'note_read_only'))).toBe(true);
    expect(showsNotFound(error(409, 'session_not_notable'))).toBe(true);
    expect(showsNotFound(error(404, 'not_found'))).toBe(true);
    expect(
      showsNotFound(
        new ApiError({
          status: 422,
          code: 'validation_error',
          fieldErrors: [
            {
              path: ['body', 'student_id'],
              code: 'student_not_participant',
              message: 'x',
            },
          ],
        }),
      ),
    ).toBe(true);
    expect(showsNotFound(error(409, 'version_conflict'))).toBe(false);
    expect(showsNotFound(new Error('x'))).toBe(false);
  });
});

describe('the notes page (H1)', () => {
  it('shows the class, the badges as text and the earlier notes', async () => {
    await open();

    expect(main().querySelector('h1')?.textContent).toBe('Session notes');
    expect(text()).toContain('Matemáticas (Secundaria)');
    expect(text()).toContain('Internal: never shown to families');
    expect(text()).toContain('Shared with family on Nov 9, 2026');
    expect(text()).toContain('Earlier notes about Ana Díaz');
    expect(text()).toContain('Le cuesta la división larga.');
    expect(text()).toContain('Luis Pérez');
    // The student selector: every participant.
    const select = main().querySelector('select')!;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'Ana Díaz',
      'Bruno Gómez',
      'Carla Ruiz',
    ]);

    await i18n.changeLanguage('es');
    await settle();
    expect(text()).toContain('Interna: nunca se muestra a las familias');
    expect(buttonNamed(main(), 'Guardar nota interna')).toBeTruthy();
  });

  it('switches the notes, the composer and the history with the student', async () => {
    await open([
      INTERNAL,
      { ...SHARED, student: { id: BRUNO, display_name: 'Bruno Gómez' } },
    ]);
    const select = main().querySelector('select')!;

    act(() => {
      select.value = BRUNO;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settleUntil(() => text().includes('Notes about Bruno Gómez'));

    expect(text()).toContain(SHARED.body);
    expect(text()).not.toContain(INTERNAL.body);
    expect(text()).toContain('New note about Bruno Gómez');
    expect(text()).toContain('Earlier notes about Bruno Gómez');
    expect(api.calls(`GET /api/v1/students/${BRUNO}/notes`)).toHaveLength(1);
  });

  it('shows only the actions its flags allow', async () => {
    await open([INTERNAL, SHARED]);
    const [internal, shared] = [...main().querySelectorAll('li')];
    const names = (li: Element) =>
      [...li.querySelectorAll('button')].map((b) => b.textContent);

    expect(names(internal!)).toEqual(['Edit', 'Share with family', 'Delete']);
    expect(names(shared!)).toEqual(['Stop sharing']);
  });

  it('shows a read-only note without actions, and the reason and Edited', async () => {
    await open([READ_ONLY, UNPUBLISHED], history());
    const [readOnly, unpublished] = [...main().querySelectorAll('li')];

    expect(readOnly!.querySelectorAll('button')).toHaveLength(0);
    expect(unpublished!.textContent).toContain(
      'Stopped sharing: Contiene un dato médico.',
    );
    expect(unpublished!.textContent).toContain('Edited');
  });

  it('renders a note as plain text', async () => {
    await open([SCRIPT], history());

    expect(text()).toContain('<script>alert(1)</script>');
    expect(main().querySelector('script')).toBeNull();
  });

  it('validates the composer before sending', async () => {
    await open();

    await click(buttonNamed(main(), 'Save internal note'));
    expect(text()).toContain('Write the note.');
    expect(composer().getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(composer());
    setValue(composer(), 'x'.repeat(4001));
    expect(text()).toContain('4001/4000 characters');
    await click(buttonNamed(main(), 'Save internal note'));
    expect(text()).toContain('At most 4000 characters.');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('saves a trimmed internal note for the selected student and reloads', async () => {
    await open();
    api.set({ [CREATE]: () => jsonResponse(201, INTERNAL) });

    setValue(composer(), '  Traer calculadora  ');
    await click(buttonNamed(main(), 'Save internal note'));
    await settleUntil(() => text().includes('Note saved.'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      student_id: ANA,
      body: 'Traer calculadora',
    });
    expect(composer().value).toBe('');
    expect(api.calls(LIST).length).toBeGreaterThan(1);
  });

  it('confirms a share with the exact text and who will see it', async () => {
    await open([INTERNAL]);
    api.set({
      [`POST /api/v1/notes/${NOTE_ID}/publish`]: () =>
        jsonResponse(200, SHARED),
    });

    await click(buttonNamed(main(), 'Share with family'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Share this note with the family?');
    expect(dialog.textContent).toContain(
      'Ana Díaz and guardians with access to their academic records',
    );
    expect(dialog.querySelector('blockquote')?.textContent).toBe(INTERNAL.body);
    expect(api.calls(`POST /api/v1/notes/${NOTE_ID}/publish`)).toHaveLength(0);

    await click(buttonNamed(dialog, 'Share with family'));
    await settleUntil(() => text().includes('Note shared with the family.'));
    expect(api.calls(`POST /api/v1/notes/${NOTE_ID}/publish`)[0]!.body).toEqual(
      { version: 1 },
    );
  });

  it('asks before deleting', async () => {
    await open([INTERNAL]);
    api.set({ [`DELETE /api/v1/notes/${NOTE_ID}`]: noContent });

    await click(buttonNamed(main(), 'Delete'));
    expect(openDialog()?.textContent).toContain('Delete this note?');
    await click(buttonNamed(openDialog()!, 'Delete note'));
    await settleUntil(() => text().includes('Note deleted.'));
    expect(api.calls(`DELETE /api/v1/notes/${NOTE_ID}`)).toHaveLength(1);
  });

  it('offers Reload when the note changed elsewhere', async () => {
    await open([SHARED]);
    api.set({
      [`POST /api/v1/notes/${SHARED_ID}/unpublish`]: () =>
        problem(409, 'version_conflict'),
    });

    await click(buttonNamed(main(), 'Stop sharing'));
    await settleUntil(() => alerts(main()).length > 0);

    expect(alerts(main()).join(' ')).toContain('Note changed elsewhere.');
    const before = api.calls(LIST).length;
    await click(buttonNamed(main(), 'Reload'));
    expect(api.calls(LIST).length).toBeGreaterThan(before);
  });

  it.each([
    [409, 'note_published'],
    [403, 'note_read_only'],
    [404, 'not_found'],
  ])('shows the not-found page for %s %s', async (status, code) => {
    await open([INTERNAL]);
    api.set({ [CREATE]: () => problem(status, code) });

    setValue(composer(), 'Nota');
    await click(buttonNamed(main(), 'Save internal note'));
    await settleUntil(() => !text().includes('Session notes'));

    expect(text()).not.toContain('Notes about Ana Díaz');
  });

  it('shows the not-found page for an unknown session', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(TUTOR),
      [ATTENDANCE]: () => problem(404, 'not_found'),
      [LIST]: () => problem(404, 'not_found'),
    });
    app = await renderApp(PATH);
    await settle();

    expect(text()).not.toContain('Session notes');
  });
});

async function renderAlone(
  node: ReturnType<typeof createElement>,
): Promise<HTMLElement> {
  app = await renderApp('/', [{ path: '/', element: node }]);
  await settle();
  return app.container;
}

describe('focus after the dialogs and actions (#79 QA D1)', () => {
  const focused = () => document.activeElement;
  const statusEl = () => main().querySelector<HTMLElement>('[role="status"]');
  const itemButton = (index: number, name: string) =>
    buttonNamed([...main().querySelectorAll('li')][index]!, name);

  it.each(['Escape', 'Cancel'])(
    'returns focus to Share with family after %s',
    async (how) => {
      await open([INTERNAL]);
      const share = itemButton(0, 'Share with family');
      share.focus();
      await click(share);
      expect(openDialog()).not.toBeNull();
      if (how === 'Escape') {
        await pressEscape();
      } else {
        await click(buttonNamed(openDialog()!, 'Cancel'));
      }
      await settleUntil(() => focused() === itemButton(0, 'Share with family'));
      expect(openDialog()).toBeNull();
    },
  );

  it.each(['Escape', 'Cancel'])(
    'returns focus to Delete after %s',
    async (how) => {
      await open([INTERNAL]);
      const remove = itemButton(0, 'Delete');
      remove.focus();
      await click(remove);
      if (how === 'Escape') {
        await pressEscape();
      } else {
        await click(buttonNamed(openDialog()!, 'Cancel'));
      }
      await settleUntil(() => focused() === itemButton(0, 'Delete'));
    },
  );

  it('moves focus to the notice after a confirmed share', async () => {
    await open([INTERNAL]);
    api.set({
      [`POST /api/v1/notes/${NOTE_ID}/publish`]: () =>
        jsonResponse(200, SHARED),
    });
    await click(itemButton(0, 'Share with family'));
    await click(buttonNamed(openDialog()!, 'Share with family'));
    await settleUntil(
      () => statusEl()?.textContent === 'Note shared with the family.',
    );
    await settleUntil(() => focused() === statusEl());
  });

  it('moves focus to the notice after a confirmed delete', async () => {
    await open([INTERNAL]);
    api.set({ [`DELETE /api/v1/notes/${NOTE_ID}`]: noContent });
    await click(itemButton(0, 'Delete'));
    await click(buttonNamed(openDialog()!, 'Delete note'));
    await settleUntil(() => statusEl()?.textContent === 'Note deleted.');
    await settleUntil(() => focused() === statusEl());
  });

  it("moves focus to the notice after the tutor's Stop sharing", async () => {
    await open([SHARED]);
    api.set({
      [`POST /api/v1/notes/${SHARED_ID}/unpublish`]: () =>
        jsonResponse(200, INTERNAL),
    });
    await click(itemButton(0, 'Stop sharing'));
    await settleUntil(
      () => statusEl()?.textContent === 'Note no longer shared.',
    );
    await settleUntil(() => focused() === statusEl());
  });

  it('moves focus to the notice after saving a note', async () => {
    await open();
    api.set({ [CREATE]: () => jsonResponse(201, INTERNAL) });
    setValue(composer(), 'Traer calculadora');
    const save = buttonNamed(main(), 'Save internal note');
    save.focus();
    await click(save);
    await settleUntil(() => statusEl()?.textContent === 'Note saved.');
    await settleUntil(() => focused() === statusEl());
  });

  it('moves focus to the failure when a write is refused', async () => {
    await open([SHARED]);
    api.set({
      [`POST /api/v1/notes/${SHARED_ID}/unpublish`]: () =>
        problem(409, 'version_conflict'),
    });
    await click(itemButton(0, 'Stop sharing'));
    await settleUntil(() =>
      Boolean(focused()?.querySelector('[role="alert"]')),
    );
    expect(focused()?.textContent).toContain('Note changed elsewhere.');
  });

  it('returns focus to Edit when the inline edit is cancelled', async () => {
    await open([INTERNAL]);
    await click(itemButton(0, 'Edit'));
    await click(itemButton(0, 'Cancel'));
    await settleUntil(() => focused() === itemButton(0, 'Edit'));
  });
});

describe('My schedule (H2)', () => {
  it.each([
    ['confirmed', true],
    ['completed', true],
    ['offered', false],
  ])('a tutor %s entry links to Notes: %s', async (state, expected) => {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const item = { ...tutorItem(), state } as ReturnType<typeof tutorItem>;
    await renderAlone(
      createElement(EntryDialog, {
        entry: { audience: 'tutor', item },
        context: {
          t: i18n.getFixedT('en', 'common'),
          timeZone: ZONE,
          language: 'en',
        },
        onClose: () => undefined,
        onReturnFocus: () => undefined,
      }),
    );
    const link = [
      ...document.querySelectorAll<HTMLAnchorElement>('[role="dialog"] a'),
    ].find((a) => a.textContent === 'Notes');
    expect(Boolean(link)).toBe(expected);
    if (link) {
      expect(link.getAttribute('href')).toBe(
        `/my-schedule/sessions/${item.id}/notes`,
      );
    }
  });

  it('lists the shared notes', async () => {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const container = await renderAlone(
      createElement(FamilyNotesList, { notes: [FAMILY_NOTE], timeZone: ZONE }),
    );
    expect(container.textContent).toContain('Notes from the tutor');
    expect(container.textContent).toContain(FAMILY_NOTE.body);
    expect(container.textContent).toContain('Carmen Ruiz, Nov 9, 2026');
  });

  it('renders nothing without shared notes', async () => {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const container = await renderAlone(
      createElement(FamilyNotesList, { notes: [], timeZone: ZONE }),
    );
    expect(container.textContent).not.toContain('Notes from the tutor');
  });

  it('shows a failure with Try again', async () => {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const retry = vi.fn();
    const container = await renderAlone(
      createElement(FamilyNotesList, {
        timeZone: ZONE,
        error: new ApiError({ status: 500, code: 'internal_error' }),
        onRetry: retry,
      }),
    );
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    await click(buttonNamed(container, 'Try again'));
    expect(retry).toHaveBeenCalled();
  });
});

describe('the staff section (H3)', () => {
  it('requires a reason before it stops sharing', async () => {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(TUTOR) });
    const stop = vi.fn(() => Promise.resolve());
    const shared = { ...SHARED, is_own: false };
    const container = await renderAlone(
      createElement(NotesSection, {
        state: { status: 'ready', notes: [shared, INTERNAL] },
        timeZone: ZONE,
        onStopSharing: stop,
        onReload: () => undefined,
      }),
    );
    expect(container.textContent).toContain('About Ana Díaz');
    expect(
      [...container.querySelectorAll('button')].map((b) => b.textContent),
    ).toContain('Stop sharing');
    expect(
      [...container.querySelectorAll('li')][1]!.querySelectorAll('button'),
    ).toHaveLength(0);

    await click(buttonNamed(container, 'Stop sharing'));
    const dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Stop sharing'));
    expect(dialog.textContent).toContain('Give a reason.');
    expect(stop).not.toHaveBeenCalled();

    setValue(dialog.querySelector('textarea')!, '  Dato médico  ');
    await click(buttonNamed(dialog, 'Stop sharing'));
    await settle();
    expect(stop).toHaveBeenCalledWith(shared, 'Dato médico');
  });

  const ID = CREATED.id;

  async function sessionPage(permissions: string[]): Promise<void> {
    api = stubApi({
      'GET /api/v1/auth/me': meAs({
        ...TEST_USER,
        permissions,
      } as SessionUserBody),
      [`GET /api/v1/class-sessions/${ID}`]: () =>
        jsonResponse(200, {
          ...CONFIRMED_SESSION,
          starts_at: '2099-01-05T16:00:00Z',
          ends_at: '2099-01-05T17:00:00Z',
        }),
      [`GET /api/v1/class-sessions/${ID}/offers`]: () =>
        jsonResponse(200, { items: [] }),
      [`GET /api/v1/class-sessions/${ID}/substitutions`]: () =>
        jsonResponse(200, { items: [] }),
      [`GET /api/v1/class-sessions/${ID}/attendance`]: () =>
        jsonResponse(200, STAFF_VIEW),
      [`GET /api/v1/class-sessions/${ID}/notes`]: () =>
        jsonResponse(200, { items: [{ ...SHARED, is_own: false }] }),
    });
    app = await renderApp(`/schedule/sessions/${ID}`);
    await settleUntil(() => text().includes('Attendance'));
    await settle();
  }

  it('is shown to academic_note.review holders', async () => {
    await sessionPage(['schedule.view', 'academic_note.review']);
    expect(
      [...main().querySelectorAll('h2')].map((h) => h.textContent),
    ).toContain('Notes');
    expect(text()).toContain(SHARED.body);
  });

  it('is not shown, nor read, for Finance', async () => {
    await sessionPage(['schedule.view', 'student.view']);
    expect(
      [...main().querySelectorAll('h2')].map((h) => h.textContent),
    ).not.toContain('Notes');
    expect(api.calls(`GET /api/v1/class-sessions/${ID}/notes`)).toHaveLength(0);
  });
});

describe('the notification links (H4)', () => {
  const recipient = { userId: TUTOR.id, timeZone: ZONE };

  it('opens My schedule on the date for a shared note', () => {
    expect(
      NOTIFICATION_LINKS['academics.learning_note_published']!(
        {
          student_id: ANA,
          class_session_id: SESSION_ID,
          starts_at: '2026-11-09T16:00:00Z',
        },
        recipient,
      ),
    ).toBe(`/my-schedule?for=${ANA}&view=list&date=2026-11-09`);
  });

  it("keeps today's link without a student id, and drops the date without a zone", () => {
    const link = NOTIFICATION_LINKS['academics.learning_note_published']!;
    const params = {
      class_session_id: SESSION_ID,
      starts_at: '2026-11-09T16:00:00Z',
    };
    expect(link(params, recipient)).toBe(
      '/my-schedule?view=list&date=2026-11-09',
    );
    expect(link({ ...params, student_id: '' }, recipient)).toBe(
      '/my-schedule?view=list&date=2026-11-09',
    );
    expect(link({ ...params, student_id: 7 }, recipient)).toBe(
      '/my-schedule?view=list&date=2026-11-09',
    );
    expect(link({ ...params, student_id: ANA }, { userId: TUTOR.id })).toBe(
      `/my-schedule?for=${ANA}&view=list`,
    );
  });

  it("opens the tutor's notes page for an unpublish", () => {
    expect(
      NOTIFICATION_LINKS['academics.learning_note_unpublished']!(
        { note_id: NOTE_ID, class_session_id: SESSION_ID },
        recipient,
      ),
    ).toBe(`/my-schedule/sessions/${SESSION_ID}/notes`);
  });
});
