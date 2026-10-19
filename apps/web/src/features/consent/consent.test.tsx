import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import { JOSE } from '@/features/students/fixtures';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import {
  GUARDIAN_GIVEN,
  GUARDIAN_NOT_GIVEN,
  LAURA_ACCESS,
  LAURA_GUARDIAN_ID,
  LAURA_LOGIN_CONSENT,
  LINKED_LOGIN,
  LOGIN_OPTIONS,
  MARTA_GUARDIAN_ID,
  STAFF_ADULT,
  STAFF_MINOR,
  STUDENT_ADULT,
} from './fixtures';

// #35 I1, I2, I4: the Login account and Consents sections of the student
// detail: hidden without permission or on 404, the dialogs for staff,
// guardian and student, only the can_record scopes, the suspension warning,
// field errors on the right control, the account dialog and its errors.

const STUDENT = `GET /api/v1/students/${JOSE.id}`;
const CONSENTS = `GET /api/v1/students/${JOSE.id}/consents`;
const RECORD = `POST /api/v1/students/${JOSE.id}/consents`;
const revokeRoute = (id: string) =>
  `POST /api/v1/students/${JOSE.id}/consents/${id}/revoke`;
const ACCOUNT = `PUT /api/v1/students/${JOSE.id}/account`;
const USERS = 'GET /api/v1/users';

const OWNER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'student.view',
    'student.manage',
    'guardian.view',
    'family.manage',
    'consent.record',
    'user.view',
  ],
};
const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['student.view', 'student.manage', 'guardian.view'],
};
const PARENT: SessionUserBody = {
  ...TEST_USER,
  roles: ['guardian'],
  permissions: ['student.view', 'guardian.view', 'consent.record'],
};
const ADULT_STUDENT: SessionUserBody = {
  ...TEST_USER,
  roles: ['student'],
  permissions: ['student.view', 'consent.record'],
};

const OWN_VIEW = { ...JOSE, guardians: null, family: null, login: null };

function stub(
  user: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [STUDENT]: () => jsonResponse(200, user === OWNER ? JOSE : OWN_VIEW),
    [CONSENTS]: () => jsonResponse(200, STAFF_MINOR),
    [USERS]: () =>
      jsonResponse(200, {
        items: LOGIN_OPTIONS,
        total: 2,
        limit: 10,
        offset: 0,
      }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const sectionTitled = (title: string) =>
  [...main().querySelectorAll('section')].find(
    (s) => s.querySelector('h2')?.textContent === title,
  );
const consents = () => sectionTitled('Consents');
const loginSection = () => sectionTitled('Login account');
const statusText = (root: ParentNode) =>
  [...root.querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');

function radio(root: ParentNode, text: string): HTMLInputElement {
  const label = [...root.querySelectorAll('label')].find((l) =>
    l.textContent?.includes(text),
  );
  const input = label?.querySelector('input[type="radio"]');
  if (!(input instanceof HTMLInputElement)) {
    throw new Error(`No option "${text}"`);
  }
  return input;
}

function select(root: ParentNode, label: string): HTMLSelectElement {
  const found = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === label,
  );
  const element = found ? document.getElementById(found.htmlFor) : null;
  if (!(element instanceof HTMLSelectElement)) {
    throw new Error(`No select labelled "${label}"`);
  }
  return element;
}

function choose(element: HTMLSelectElement, value: string) {
  act(() => {
    element.value = value;
    element.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function afterDebounce() {
  await act(async () => {
    await new Promise((resolve) =>
      setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
    );
  });
  await settle();
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
});

describe('visibility', () => {
  it('without consent.record or family.manage neither section shows', async () => {
    const api = stub(COORDINATOR);

    app = await renderApp(`/students/${JOSE.id}`);

    expect(consents()).toBeUndefined();
    expect(loginSection()).toBeUndefined();
    expect(api.calls(CONSENTS)).toHaveLength(0);
  });

  it('the consent section hides on 404', async () => {
    stub(PARENT, { [CONSENTS]: () => problem(404, 'not_found') });

    app = await renderApp(`/students/${JOSE.id}`);

    expect(main().textContent).toContain('José Ñúñez Pérez');
    expect(consents()).toBeUndefined();
    expect(loginSection()).toBeUndefined();
  });
});

describe('staff (I2)', () => {
  it('shows the summary, the records and offers only can_record scopes', async () => {
    stub(OWNER);
    app = await renderApp(`/students/${JOSE.id}`);

    const text = consents()!.textContent ?? '';
    expect(text).toContain("Minor: a login needs a guardian's consent.");
    expect(text).toContain('Login account: Active');
    expect(text).toContain('Student login');
    expect(text).toContain('Laura Ñúñez Prieto');
    expect(text).toContain('In the app');
    expect(text).toContain('Privacy notice version 1');
    expect(text).toContain('Active');
    expect(text).toContain('Revoked on');

    await click(buttonNamed(consents()!, 'Record consent'));
    const scopes = [...dialog()!.querySelectorAll('fieldset')][0]!;
    expect(
      [...scopes.querySelectorAll('label')].map((l) => l.textContent),
    ).toEqual(['Student login']);
    const options = [
      ...select(dialog()!, 'Guardian').querySelectorAll('option'),
    ].map((o) => o.textContent);
    // Only the active guardians (Ramón is inactive).
    expect(options).toEqual([
      'Choose a guardian',
      'Laura Ñúñez Prieto · Mother',
      'Marta Solís Ruiz · Legal guardian',
    ]);
    expect(dialog()!.textContent).toContain('Privacy notice version 1');
    expect(() => radio(dialog()!, 'In the app')).toThrow();
  });

  it('records a paper consent; a server field error lands on its control', async () => {
    const api = stub(OWNER, {
      [RECORD]: [
        () => problem(422, 'consent_guardian_invalid'),
        () => jsonResponse(201, LAURA_LOGIN_CONSENT),
      ],
    });
    app = await renderApp(`/students/${JOSE.id}`);
    const before = api.calls(CONSENTS).length;

    await click(buttonNamed(consents()!, 'Record consent'));
    await click(buttonNamed(dialog()!, 'Record'));
    expect(dialog()!.textContent).toContain('Choose a guardian');
    expect(dialog()!.textContent).toContain('Choose a method.');
    expect(api.calls(RECORD)).toHaveLength(0);

    choose(select(dialog()!, 'Guardian'), MARTA_GUARDIAN_ID);
    await click(radio(dialog()!, 'On paper'));
    await click(buttonNamed(dialog()!, 'Record'));

    const guardian = select(dialog()!, 'Guardian');
    expect(guardian.getAttribute('aria-invalid')).toBe('true');
    const described = document.getElementById(
      guardian.getAttribute('aria-describedby')!,
    );
    expect(described?.textContent).toBe(
      'Choose an active guardian linked to this student.',
    );
    await click(buttonNamed(dialog()!, 'Record'));

    expect(api.calls(RECORD).map((c) => c.body)).toEqual([
      {
        scope: 'student_login',
        guardian_id: MARTA_GUARDIAN_ID,
        method: 'paper',
      },
      {
        scope: 'student_login',
        guardian_id: MARTA_GUARDIAN_ID,
        method: 'paper',
      },
    ]);
    expect(dialog()).toBeNull();
    expect(api.calls(CONSENTS).length).toBeGreaterThan(before);
    expect(statusText(consents()!)).toBe('Consent recorded.');
  });

  it('a method error lands on the method group', async () => {
    stub(OWNER, { [RECORD]: () => problem(422, 'consent_method_invalid') });
    app = await renderApp(`/students/${JOSE.id}`);

    await click(buttonNamed(consents()!, 'Record consent'));
    choose(select(dialog()!, 'Guardian'), LAURA_GUARDIAN_ID);
    await click(radio(dialog()!, 'In person'));
    await click(buttonNamed(dialog()!, 'Record'));

    const method = [...dialog()!.querySelectorAll('fieldset')][1]!;
    const described = document.getElementById(
      method.getAttribute('aria-describedby')!,
    );
    expect(described?.textContent).toBe("That method isn't allowed.");
    expect(dialog()!.querySelector('[role="alert"]')).toBeNull();
  });

  it('revoking warns when the login would be suspended, then announces it', async () => {
    const api = stub(OWNER, {
      [revokeRoute(LAURA_LOGIN_CONSENT.id)]: () =>
        jsonResponse(200, {
          consent: {
            ...LAURA_LOGIN_CONSENT,
            revoked_at: '2026-10-21T10:00:00Z',
          },
          student_login_suspended: true,
        }),
    });
    app = await renderApp(`/students/${JOSE.id}`);

    await click(
      buttonNamed(consents()!, 'Revoke Student login: Laura Ñúñez Prieto'),
    );
    expect(dialog()!.textContent).toContain("José's login will be suspended.");
    expect(api.calls(revokeRoute(LAURA_LOGIN_CONSENT.id))).toHaveLength(0);
    await click(buttonNamed(dialog()!, 'Revoke'));

    expect(api.calls(revokeRoute(LAURA_LOGIN_CONSENT.id))).toHaveLength(1);
    expect(dialog()).toBeNull();
    expect(statusText(consents()!)).toBe(
      "Consent revoked. The student's login was suspended.",
    );
  });

  it('an adult offers academic access only, and shows the empty text', async () => {
    stub(OWNER, { [CONSENTS]: () => jsonResponse(200, STAFF_ADULT) });
    app = await renderApp(`/students/${JOSE.id}`);

    expect(consents()!.textContent).toContain(
      "Adult: guardians see academic records only with the student's consent.",
    );
    expect(consents()!.textContent).toContain('No consents recorded yet.');
    await click(buttonNamed(consents()!, 'Record consent'));
    expect(radio(dialog()!, 'Access to academic records')).toBeTruthy();
    expect(() => radio(dialog()!, 'Student login')).toThrow();
  });
});

describe('guardian (I2)', () => {
  it('allows a login in the app after a confirmation naming the notice version', async () => {
    const api = stub(PARENT, {
      [CONSENTS]: [
        () => jsonResponse(200, GUARDIAN_NOT_GIVEN),
        () => jsonResponse(200, GUARDIAN_GIVEN),
      ],
      [RECORD]: () => jsonResponse(201, LAURA_LOGIN_CONSENT),
    });
    app = await renderApp(`/students/${JOSE.id}`);

    expect(consents()!.querySelectorAll('button')).toHaveLength(1);
    await click(buttonNamed(consents()!, 'Allow José to have a login'));
    expect(dialog()!.textContent).toContain(
      'I allow José to have a Classora login, under privacy notice version 1.',
    );
    await click(buttonNamed(dialog()!, 'Confirm'));

    expect(api.calls(RECORD).map((c) => c.body)).toEqual([
      {
        scope: 'student_login',
        guardian_id: LAURA_GUARDIAN_ID,
        method: 'in_app',
      },
    ]);
    expect(consents()!.textContent).toContain('You allowed a login on');
    expect(buttonNamed(consents()!, 'Withdraw consent')).toBeTruthy();
    // Guardians revoke through "Withdraw consent", not a row button.
    expect(consents()!.querySelectorAll('button')).toHaveLength(1);
  });

  it('withdrawing warns about the suspension', async () => {
    const api = stub(PARENT, {
      [CONSENTS]: () => jsonResponse(200, GUARDIAN_GIVEN),
      [revokeRoute(LAURA_LOGIN_CONSENT.id)]: () =>
        jsonResponse(200, {
          consent: LAURA_LOGIN_CONSENT,
          student_login_suspended: true,
        }),
    });
    app = await renderApp(`/students/${JOSE.id}`);

    await click(buttonNamed(consents()!, 'Withdraw consent'));
    expect(dialog()!.textContent).toContain("José's login will be suspended.");
    await click(buttonNamed(dialog()!, 'Withdraw consent'));

    expect(api.calls(revokeRoute(LAURA_LOGIN_CONSENT.id))).toHaveLength(1);
  });
});

describe('adult student (I2)', () => {
  it('shares with one guardian and stops sharing with another', async () => {
    const api = stub(ADULT_STUDENT, {
      [CONSENTS]: () => jsonResponse(200, STUDENT_ADULT),
      [RECORD]: () => jsonResponse(201, LAURA_ACCESS),
      [revokeRoute(LAURA_ACCESS.id)]: () =>
        jsonResponse(200, {
          consent: LAURA_ACCESS,
          student_login_suspended: false,
        }),
    });
    app = await renderApp(`/students/${JOSE.id}`);

    expect(consents()!.textContent).toContain('Shared since');
    await click(
      buttonNamed(consents()!, 'Share my academic records: Marta Solís Ruiz'),
    );
    expect(dialog()!.textContent).toContain('privacy notice version 2');
    await click(buttonNamed(dialog()!, 'Confirm'));
    expect(api.calls(RECORD).map((c) => c.body)).toEqual([
      {
        scope: 'guardian_academic_access',
        guardian_id: MARTA_GUARDIAN_ID,
        method: 'in_app',
      },
    ]);

    await click(buttonNamed(consents()!, 'Stop sharing: Laura Ñúñez Prieto'));
    expect(dialog()!.textContent).not.toContain('will be suspended');
    await click(buttonNamed(dialog()!, 'Stop sharing'));
    expect(api.calls(revokeRoute(LAURA_ACCESS.id))).toHaveLength(1);
  });
});

describe('login account (I1)', () => {
  it('links an account with the version and shows errors inside the dialog', async () => {
    const api = stub(OWNER, {
      [ACCOUNT]: [
        () => problem(409, 'guardian_consent_required'),
        () => problem(409, 'student_account_in_use'),
        () => jsonResponse(200, { ...JOSE, login: LINKED_LOGIN, version: 4 }),
      ],
    });
    app = await renderApp(`/students/${JOSE.id}`);

    expect(loginSection()!.textContent).toContain('No login account');
    expect(loginSection()!.textContent).toContain(
      'The login works only after a linked guardian consents.',
    );
    await click(buttonNamed(loginSection()!, 'Link account'));
    await click(buttonNamed(dialog()!, 'Link'));
    expect(dialog()!.textContent).toContain('Choose an account.');

    typeInto(
      fieldByLabel(dialog()!, 'Search accounts by username or name'),
      'jose',
    );
    await afterDebounce();
    const archived = radio(dialog()!, 'jose.old');
    expect(archived.disabled).toBe(true);
    expect(dialog()!.textContent).toContain('Student');
    await click(radio(dialog()!, 'jose.nunez'));
    await click(buttonNamed(dialog()!, 'Link'));
    expect(dialog()!.querySelector('[role="alert"]')?.textContent).toContain(
      'The student is a minor and needs consent from a linked guardian.',
    );

    await click(buttonNamed(dialog()!, 'Link'));
    expect(dialog()!.textContent).toContain(
      'That account is already linked to another student.',
    );
    expect(dialog()!.querySelector('[role="alert"]')).toBeNull();

    await click(buttonNamed(dialog()!, 'Link'));
    expect(api.calls(ACCOUNT).map((c) => c.body)).toEqual([
      { user_id: LINKED_LOGIN.user_id, version: JOSE.version },
      { user_id: LINKED_LOGIN.user_id, version: JOSE.version },
      { user_id: LINKED_LOGIN.user_id, version: JOSE.version },
    ]);
    expect(dialog()).toBeNull();
    expect(loginSection()!.textContent).toContain('jose.nunez');
    expect(loginSection()!.textContent).toContain('Pending');
    expect(statusText(loginSection()!)).toBe('Login account linked.');
  });

  it('removes the account behind a confirmation', async () => {
    const api = stub(OWNER, {
      [STUDENT]: () => jsonResponse(200, { ...JOSE, login: LINKED_LOGIN }),
      [ACCOUNT]: () => jsonResponse(200, { ...JOSE, login: null, version: 4 }),
    });
    app = await renderApp(`/students/${JOSE.id}`);

    await click(buttonNamed(loginSection()!, 'Remove'));
    expect(dialog()!.textContent).toContain(
      "jose.nunez will no longer see this student's record.",
    );
    await click(buttonNamed(dialog()!, 'Remove'));

    expect(api.calls(ACCOUNT).map((c) => c.body)).toEqual([
      { user_id: null, version: JOSE.version },
    ]);
    expect(loginSection()!.textContent).toContain('No login account');
    expect(statusText(loginSection()!)).toBe('Login account removed.');
  });
});

describe('translations (J1)', () => {
  it('shows Spanish text', async () => {
    applyLanguage('es');
    stub(OWNER);
    app = await renderApp(`/students/${JOSE.id}`);

    expect(consents()).toBeUndefined();
    expect(sectionTitled('Consentimientos')!.textContent).toContain(
      'Menor de edad: la cuenta necesita el consentimiento de un padre o tutor.',
    );
    expect(sectionTitled('Cuenta de acceso')).toBeTruthy();
  });
});
