import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import type { ApprovalRequestItem } from '@/features/approvals/api';
import {
  APPROVAL_REVIEW_PERMISSIONS,
  reasonRequired,
} from '@/features/approvals/api';
import { approvalSummary, typeLabel } from '@/features/approvals/summary';
import {
  NOTIFICATION_LINKS,
  notificationMessage,
} from '@/features/notifications/messages';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, openDialog } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { TutorDetail } from './api';
import {
  LEVEL_PREPARATORIA,
  LEVEL_SECUNDARIA,
  QUALIFICATIONS,
  SUBJECT_ALGEBRA,
  SUBJECT_CALCULO,
  SUBJECT_FISICA,
  TUTOR_AS_ADMIN,
  TUTOR_AS_SELF,
  TUTOR_ID,
  TUTOR_ITEM,
} from './fixtures';

// #44 F1-F3: the Qualifications section of the tutor profile (table,
// teaching subjects, the request dialog with level and subject selects, the
// cancel action), the approvals summary of staff.qualification items, the
// two notification messages in es and en, and their link.

const DETAIL = `GET /api/v1/tutors/${TUTOR_ID}`;
const QUALIFICATIONS_ROUTE = `GET /api/v1/tutors/${TUTOR_ID}/qualifications`;
const REQUEST = `POST /api/v1/tutors/${TUTOR_ID}/qualifications`;
const PENDING = QUALIFICATIONS[1]!;
const CANCEL = `POST /api/v1/tutors/${TUTOR_ID}/qualifications/${PENDING.id}/cancel`;
const LEVELS = 'GET /api/v1/education-levels';
const SUBJECTS = 'GET /api/v1/subjects';

const ADMIN: SessionUserBody = {
  ...TEST_USER,
  roles: ['administrator'],
  permissions: ['tutor.view', 'tutor.manage', 'qualification.review'],
};

const SELF: SessionUserBody = {
  ...TEST_USER,
  id: TUTOR_ID,
  username: TUTOR_ITEM.username,
  display_name: TUTOR_ITEM.display_name,
  roles: ['tutor'],
  permissions: ['student.view', 'tutor.view'],
};

function level(option: { id: string; name: string }, sortOrder: number) {
  return {
    id: option.id,
    name: option.name,
    sort_order: sortOrder,
    is_active: true,
    active_subject_count: 2,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function subject(
  option: { id: string; name: string },
  levelOption: { id: string; name: string },
  isActive = true,
) {
  return {
    id: option.id,
    education_level_id: levelOption.id,
    education_level_name: levelOption.name,
    name: option.name,
    is_active: isActive,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

const page = <T,>(items: T[]) => ({
  items,
  total: items.length,
  limit: 100,
  offset: 0,
});

let app: RenderedApp | undefined;

const section = () => document.getElementById('qualifications')!;

function stub(
  user: SessionUserBody,
  detail: TutorDetail,
  routes: Record<string, Responder | Responder[]> = {},
) {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [DETAIL]: () => jsonResponse(200, detail),
    [QUALIFICATIONS_ROUTE]: () => jsonResponse(200, QUALIFICATIONS),
    [LEVELS]: () =>
      jsonResponse(
        200,
        page([level(LEVEL_SECUNDARIA, 10), level(LEVEL_PREPARATORIA, 20)]),
      ),
    // The API filters by level and status; the client filters again.
    [SUBJECTS]: (request) => {
      const levelId = new URL(request.url).searchParams.get(
        'education_level_id',
      );
      return jsonResponse(
        200,
        page(
          levelId === LEVEL_SECUNDARIA.id
            ? [
                subject(SUBJECT_ALGEBRA, LEVEL_SECUNDARIA),
                subject(SUBJECT_FISICA, LEVEL_SECUNDARIA),
                subject(SUBJECT_CALCULO, LEVEL_SECUNDARIA, false),
                subject(SUBJECT_CALCULO, LEVEL_PREPARATORIA),
              ]
            : [subject(SUBJECT_CALCULO, LEVEL_PREPARATORIA)],
        ),
      );
    },
    ...routes,
  });
}

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function dialogSelects(): HTMLSelectElement[] {
  return [...openDialog()!.querySelectorAll('select')];
}

const optionTexts = (select: HTMLSelectElement) =>
  [...select.options].map((option) => option.textContent);

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

describe('the Qualifications section (F1)', () => {
  it('lists every request with its status, dates and rejection reason', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN);

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const rows = [...section().querySelectorAll('tbody tr')];
    expect(rows).toHaveLength(4);
    const cells = (row: Element) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent);
    expect(cells(rows[0]!).slice(0, 3)).toEqual([
      'Álgebra',
      'Secundaria',
      'Approved',
    ]);
    expect(cells(rows[2]!)[2]).toBe('Rejected');
    expect(cells(rows[2]!)[5]).toBe(
      'Please add a certificate for calculus first.',
    );
    expect(cells(rows[3]!)[2]).toBe('Cancelled');
    // Teaching subjects: approved only.
    expect(
      [...section().querySelectorAll('ul li')].map((li) => li.textContent),
    ).toEqual(['Álgebra (Secundaria)']);
  });

  it('an Administrator sees no request or cancel buttons', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN);

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const names = [...section().querySelectorAll('button')].map(
      (button) => button.textContent,
    );
    expect(names).not.toContain('Request qualification');
    expect(names).not.toContain('Cancel request');
  });

  it('shows the empty states', async () => {
    stub(
      SELF,
      { ...TUTOR_AS_SELF, teaching_subjects: [] },
      {
        [QUALIFICATIONS_ROUTE]: () => jsonResponse(200, []),
      },
    );

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(section().textContent).toContain('No approved qualifications yet.');
    expect(section().textContent).toContain('No qualification requests yet.');
  });
});

describe('the request dialog (F1, F3)', () => {
  it('offers the active subjects of the chosen level and sends the subject', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, {
      [REQUEST]: () => jsonResponse(201, PENDING),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Request qualification'));
    const [levelSelect, subjectSelect] = dialogSelects();
    expect(optionTexts(levelSelect!)).toEqual([
      'Choose a level',
      'Secundaria',
      'Preparatoria',
    ]);
    expect(subjectSelect!.disabled).toBe(true);

    choose(levelSelect!, LEVEL_SECUNDARIA.id);
    await settle();

    const subjectQuery = new URL(
      api.fetch.mock.calls
        .map(([request]) => request.url)
        .find((url) => new URL(url).pathname === '/api/v1/subjects')!,
    ).searchParams;
    expect(subjectQuery.get('education_level_id')).toBe(LEVEL_SECUNDARIA.id);
    expect(subjectQuery.get('status')).toBe('active');
    // The inactive subject and another level's subject are never offered.
    expect(optionTexts(dialogSelects()[1]!)).toEqual([
      'Choose a subject',
      'Álgebra',
      'Física',
    ]);

    choose(dialogSelects()[1]!, SUBJECT_FISICA.id);
    await click(buttonNamed(openDialog()!, 'Send request'));

    expect(api.calls(REQUEST).map((r) => r.body)).toEqual([
      { subject_id: SUBJECT_FISICA.id },
    ]);
    expect(openDialog()).toBeNull();
    expect(section().querySelector('[role="status"]')?.textContent).toBe(
      'Request sent.',
    );
    // The list is fetched again.
    expect(api.calls(QUALIFICATIONS_ROUTE).length).toBeGreaterThan(1);
  });

  it('changing the level clears the subject', async () => {
    stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Request qualification'));

    choose(dialogSelects()[0]!, LEVEL_SECUNDARIA.id);
    await settle();
    choose(dialogSelects()[1]!, SUBJECT_ALGEBRA.id);
    choose(dialogSelects()[0]!, LEVEL_PREPARATORIA.id);
    await settle();

    expect(dialogSelects()[1]!.value).toBe('');
    expect(optionTexts(dialogSelects()[1]!)).toEqual([
      'Choose a subject',
      'Cálculo',
    ]);
  });

  it('without a subject: a field error and no request', async () => {
    const api = stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Request qualification'));

    await click(buttonNamed(openDialog()!, 'Send request'));

    expect(openDialog()!.textContent).toContain('Choose a subject.');
    expect(api.calls(REQUEST)).toHaveLength(0);
  });

  it('qualification_exists shows in the dialog alert', async () => {
    stub(SELF, TUTOR_AS_SELF, {
      [REQUEST]: () => problem(409, 'qualification_exists'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await click(buttonNamed(section(), 'Request qualification'));
    choose(dialogSelects()[0]!, LEVEL_SECUNDARIA.id);
    await settle();
    choose(dialogSelects()[1]!, SUBJECT_FISICA.id);

    await click(buttonNamed(openDialog()!, 'Send request'));

    expect(openDialog()).not.toBeNull();
    expect(alerts(openDialog()!).join(' ')).toContain(
      'There is already a pending or approved qualification for this subject.',
    );
  });
});

describe('cancelling a pending request (F1, F3)', () => {
  it('only pending rows have Cancel request; confirming sends it', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, {
      [CANCEL]: () =>
        jsonResponse(200, { ...PENDING, status: 'cancelled', version: 2 }),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    const cancelButtons = [...section().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Cancel request',
    );
    expect(cancelButtons).toHaveLength(1);
    expect(cancelButtons[0]!.getAttribute('aria-label')).toBe(
      'Cancel request for Física',
    );

    await click(cancelButtons[0]!);
    expect(openDialog()!.textContent).toContain(
      'The request for Física (Secundaria) will be withdrawn.',
    );
    await click(buttonNamed(openDialog()!, 'Cancel request'));

    expect(api.calls(CANCEL)).toHaveLength(1);
    expect(openDialog()).toBeNull();
    expect(section().querySelector('[role="status"]')?.textContent).toBe(
      'Request cancelled.',
    );
  });

  it('Keep request closes without a call', async () => {
    const api = stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Cancel request for Física'));
    await click(buttonNamed(openDialog()!, 'Keep request'));

    expect(openDialog()).toBeNull();
    expect(api.calls(CANCEL)).toHaveLength(0);
  });

  it('a 409 shows in the section alert', async () => {
    stub(SELF, TUTOR_AS_SELF, {
      [CANCEL]: () => problem(409, 'approval_transition_invalid'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await click(buttonNamed(section(), 'Cancel request for Física'));
    await click(buttonNamed(openDialog()!, 'Cancel request'));

    expect(alerts(section()).length).toBe(1);
  });
});

function qualificationItem(): ApprovalRequestItem {
  return {
    id: '00000000-0000-4000-8000-000000000a01',
    request_type: 'staff.qualification',
    status: 'pending',
    priority: 'normal',
    requester: { id: TUTOR_ID, display_name: 'Lucía Méndez' },
    assignee: null,
    created_at: '2026-10-01T15:00:00Z',
    resolved_at: null,
    resolved_by: null,
    reason: null,
    subject: {
      tutor_display_name: 'Lucía Méndez',
      subject_name: 'Física',
      education_level_name: 'Secundaria',
    },
    allowed_actions: ['approve', 'reject'],
    version: 1,
  };
}

describe('approvals and notifications (F2, F3, C4)', () => {
  it('the approvals summary and type label of a qualification', () => {
    const t = i18n.getFixedT('en');
    expect(approvalSummary(qualificationItem(), t)).toBe(
      'Lucía Méndez — Física (Secundaria)',
    );
    expect(typeLabel('staff.qualification', t)).toBe('Tutor qualification');
    expect(typeLabel('staff.qualification', i18n.getFixedT('es'))).toBe(
      'Calificación de tutor',
    );
    // A malformed subject falls back to the type code.
    expect(approvalSummary({ ...qualificationItem(), subject: {} }, t)).toBe(
      'staff.qualification',
    );
  });

  it('qualification.review opens the approvals page; reject needs a reason', () => {
    expect(APPROVAL_REVIEW_PERMISSIONS).toContain('qualification.review');
    expect(reasonRequired('staff.qualification', 'reject')).toBe(true);
    expect(reasonRequired('staff.qualification', 'approve')).toBe(false);
  });

  it('the decision messages in es and en', () => {
    const params = {
      subject_name: 'Física',
      education_level_name: 'Secundaria',
    };
    const message = (lng: string, type: string) =>
      notificationMessage(i18n.getFixedT(lng), i18n, {
        notification_type: type,
        params,
      });
    expect(message('es', 'staff.qualification_approved')).toBe(
      'Se aprobó tu calificación para Física (Secundaria).',
    );
    expect(message('en', 'staff.qualification_approved')).toBe(
      'Your qualification for Física (Secundaria) was approved.',
    );
    expect(message('es', 'staff.qualification_rejected')).toBe(
      'No se aprobó tu calificación para Física (Secundaria); consulta el motivo en tus calificaciones.',
    );
    expect(message('en', 'staff.qualification_rejected')).toBe(
      'Your qualification for Física (Secundaria) was not approved; see the reason in your qualifications.',
    );
  });

  it("both link to the recipient's own qualifications", () => {
    for (const type of [
      'staff.qualification_approved',
      'staff.qualification_rejected',
    ]) {
      expect(NOTIFICATION_LINKS[type]?.({}, { userId: TUTOR_ID })).toBe(
        `/tutors/${TUTOR_ID}#qualifications`,
      );
    }
  });
});
