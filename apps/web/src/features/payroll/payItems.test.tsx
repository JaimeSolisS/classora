import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import {
  meAs,
  noContent,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  alerts,
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
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { PayItem, PayItemPage, PayItemType } from './api';
import {
  HOURLY,
  ITEM_ENDED,
  ITEM_LONG,
  ITEM_PAID,
  ITEM_PLACED,
  ITEM_PREPARED,
  ITEM_RECURRING,
  ITEM_WAITING,
  PAY_ITEM_TYPES,
  PAY_ITEMS,
  payItemPage,
  TUTOR_ID,
  TYPE_BONUS,
  TYPE_DEDUCTION,
  TYPE_RETIRED,
} from './fixtures';
import {
  emptyPayItemForm,
  initialPayItemEnd,
  payItemCreateBody,
  validatePayItemEnd,
  validatePayItemForm,
  validatePayItemTypeForm,
} from './payItemForm';

// #454: the types page and the tutor's section: lists, empty states, each
// dialog's success and refusal, own-page read-only, permission hiding, B2's
// four states, the idempotency key of B4, and that a change refreshes only
// the items (B8). Amounts on screen are the fixtures' strings, formatted.

const ME = 'GET /api/v1/auth/me';
const TYPES = 'GET /api/v1/payroll/pay-item-types';
const TYPE_POST = 'POST /api/v1/payroll/pay-item-types';
const typePatch = (id: string) => `PATCH /api/v1/payroll/pay-item-types/${id}`;
const COMPENSATION = `GET /api/v1/payroll/tutors/${TUTOR_ID}/compensation`;
const ITEMS = `GET /api/v1/payroll/tutors/${TUTOR_ID}/pay-items`;
const ITEM_POST = `POST /api/v1/payroll/tutors/${TUTOR_ID}/pay-items`;
const itemRoute = (method: string, id: string) =>
  `${method} /api/v1/payroll/tutors/${TUTOR_ID}/pay-items/${id}`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
  id = TEST_USER.id,
): SessionUserBody => ({ ...TEST_USER, id, roles, permissions });
const FINANCE = user(['finance_staff'], ['payroll.view', 'payroll.manage']);
const VIEWER = user(['finance_staff'], ['payroll.view']);
const OWN_PAY = user(
  ['finance_staff', 'tutor'],
  ['payroll.view', 'payroll.manage'],
  TUTOR_ID,
);
const OWNER_TUTOR = user(
  ['owner', 'tutor'],
  ['payroll.view', 'payroll.manage'],
  TUTOR_ID,
);

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    as = FINANCE,
    items = PAY_ITEMS,
    types = PAY_ITEM_TYPES,
  }: {
    as?: SessionUserBody;
    items?: PayItem[];
    types?: PayItemType[];
  } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [COMPENSATION]: () => jsonResponse(200, HOURLY),
    [ITEMS]: () => jsonResponse(200, payItemPage(items)),
    [TYPES]: () => jsonResponse(200, types),
    ...routes,
  });
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const dialog = () => openDialog()!;
const section = () =>
  main().querySelector<HTMLElement>('[data-pay-items-section]')!;
const row = (item: PayItem) =>
  section().querySelector<HTMLElement>(
    `[data-pay-items-table] [data-pay-item-id="${item.id}"]`,
  )!;
const typeRow = (type: PayItemType) =>
  main().querySelector<HTMLElement>(
    `[data-pay-types-table] [data-pay-type-id="${type.id}"]`,
  )!;
const named = (root: ParentNode, name: string) =>
  [...root.querySelectorAll<HTMLButtonElement>('button')].find(
    (button) =>
      button.textContent === name || button.getAttribute('aria-label') === name,
  );
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((node) => node.textContent)
    .join(' ');

async function openPage() {
  app = await renderApp(`/payroll/tutors/${TUTOR_ID}`);
  await settleUntil(() => section());
  await settle();
}

async function openTypes() {
  app = await renderApp('/payroll/pay-item-types');
  await settleUntil(() => main().querySelector('h1'));
  await settle();
}

async function openAdd() {
  await openPage();
  await click(named(section(), 'Add bonus or deduction')!);
  await settleUntil(() => openDialog());
  await settle();
}

function fillOneTime(amount = '500') {
  const form = dialog();
  setFieldValue(labelled(form, 'Type'), TYPE_BONUS.id);
  setFieldValue(labelled(form, 'Description'), '  Sin faltas  ');
  setFieldValue(labelled(form, 'Amount (MXN)'), amount);
}

const posted = (api: ApiStub, route: string) => api.calls(route);

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the rules', () => {
  const values = {
    ...emptyPayItemForm('2026-10-20' as LocalDate),
    typeId: TYPE_BONUS.id,
    description: ' x ',
    amount: ' 500 ',
  };

  it('sends a one-time item with no end, trimmed', () => {
    expect(payItemCreateBody({ ...values, effectiveTo: '2026-12-01' })).toEqual(
      {
        pay_item_type_id: TYPE_BONUS.id,
        description: 'x',
        amount: '500',
        recurrence: 'one_time',
        effective_from: '2026-10-20',
      },
    );
  });

  it('sends a recurring end only when there is one', () => {
    const recurring = { ...values, recurrence: 'recurring' } as const;
    expect(payItemCreateBody(recurring)).not.toHaveProperty('effective_to');
    expect(
      payItemCreateBody({ ...recurring, effectiveTo: '2026-12-01' })
        .effective_to,
    ).toBe('2026-12-01');
  });

  it.each([
    ['', 'amountRequired'],
    ['0', 'amountPositive'],
    ['0.00', 'amountPositive'],
    ['-5', 'amountInvalid'],
    ['1.234', 'amountInvalid'],
  ])('refuses the amount %j', (amount, key) => {
    expect(validatePayItemForm({ ...values, amount }).amount).toBe(
      `payroll.validation.${key}`,
    );
  });

  it('counts the limits as the API does, in code points', () => {
    expect(
      validatePayItemForm({ ...values, description: '😀'.repeat(500) })
        .description,
    ).toBeUndefined();
    expect(
      validatePayItemForm({ ...values, description: 'x'.repeat(501) })
        .description,
    ).toBe('payroll.validation.descriptionTooLong');
    expect(
      validatePayItemTypeForm({ name: 'x'.repeat(101), direction: 'bonus' })
        .name,
    ).toBe('payroll.validation.nameTooLong');
  });

  it('refuses a recurring end before the start, and ignores a hidden end', () => {
    const bad = { ...values, effectiveTo: '2026-10-19' };
    expect(
      validatePayItemForm({ ...bad, recurrence: 'recurring' }).effectiveTo,
    ).toBe('errors:effective_to_before_from');
    expect(validatePayItemForm(bad).effectiveTo).toBeUndefined();
  });

  it('starts the end at the current end, else today, never before the start', () => {
    const today = '2026-10-20' as LocalDate;
    expect(initialPayItemEnd(ITEM_ENDED, today)).toBe('2026-12-31');
    expect(initialPayItemEnd(ITEM_RECURRING, today)).toBe(today);
    expect(
      initialPayItemEnd(
        { effective_from: '2026-11-01', effective_to: null },
        today,
      ),
    ).toBe('2026-11-01');
  });

  it('refuses an end before the start, or no date', () => {
    expect(validatePayItemEnd('', ITEM_RECURRING)).toBe(
      'payroll.validation.dateRequired',
    );
    expect(validatePayItemEnd('2026-08-31', ITEM_RECURRING)).toBe(
      'errors:pay_item_end_before_start',
    );
    expect(validatePayItemEnd('2026-09-01', ITEM_RECURRING)).toBeUndefined();
  });
});

describe('types page (A)', () => {
  it('lists name, direction and state in the API order', async () => {
    stub();
    await openTypes();
    const rows = [
      ...main().querySelectorAll('[data-pay-types-table] tbody tr'),
    ].map((tr) => tr.textContent);
    expect(rows[0]).toContain('Bono de puntualidad');
    expect(rows[0]).toContain('Bonus');
    expect(rows[0]).toContain('Active');
    expect(rows[1]).toContain('Deduction');
    expect(rows[2]).toContain('Inactive');
    expect(main().querySelector('a[aria-current="page"]')?.textContent).toBe(
      'Bonus and deduction types',
    );
  });

  it('explains that no types are seeded, and offers to add one', async () => {
    stub({}, { types: [] });
    await openTypes();
    expect(main().textContent).toContain('none come preset');
    expect(named(main(), 'Add type')).toBeDefined();
  });

  it('shows no control without payroll.manage', async () => {
    stub({}, { as: VIEWER });
    await openTypes();
    expect(main().querySelector('[data-pay-types-table]')).not.toBeNull();
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('shows the error with a retry', async () => {
    stub({ [TYPES]: () => problem(403, 'permission_denied') });
    await openTypes();
    expect(alerts(main()).length).toBeGreaterThan(0);
  });

  it('adds a type, trimmed, and refreshes', async () => {
    const api = stub({
      [TYPE_POST]: () => jsonResponse(201, TYPE_BONUS),
    });
    await openTypes();
    await click(named(main(), 'Add type')!);
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Enter the name.');
    expect(dialog().textContent).toContain('Choose bonus or deduction.');
    expect(posted(api, TYPE_POST)).toHaveLength(0);

    setFieldValue(labelled(dialog(), 'Name'), '  Bono  ');
    setFieldValue(labelled(dialog(), 'Direction'), 'bonus');
    const reads = posted(api, TYPES).length;
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(posted(api, TYPE_POST)[0]!.body).toEqual({
      name: 'Bono',
      direction: 'bonus',
    });
    expect(posted(api, TYPES).length).toBeGreaterThan(reads);
    expect(statusText()).toContain('Type saved.');
  });

  it.each([['pay_item_type_exists', 'A type with this name already exists']])(
    'puts %s on the name',
    async (code, text) => {
      stub({ [TYPE_POST]: () => problem(409, code) });
      await openTypes();
      await click(named(main(), 'Add type')!);
      await settleUntil(() => openDialog());
      setFieldValue(labelled(dialog(), 'Name'), 'Bono');
      setFieldValue(labelled(dialog(), 'Direction'), 'bonus');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(() => dialog().textContent?.includes(text));
      expect(labelled(dialog(), 'Name').getAttribute('aria-invalid')).toBe(
        'true',
      );
    },
  );

  it.each([['text_invalid'], ['text_empty'], ['text_too_long']])(
    'puts the field error %s on the name, with a mapped message',
    async (code) => {
      stub({
        [TYPE_POST]: () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: ['body', 'name'], code, message: 'x' }],
          }),
      });
      await openTypes();
      await click(named(main(), 'Add type')!);
      await settleUntil(() => openDialog());
      setFieldValue(labelled(dialog(), 'Name'), 'Bono');
      setFieldValue(labelled(dialog(), 'Direction'), 'bonus');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(
        () =>
          labelled(dialog(), 'Name').getAttribute('aria-invalid') === 'true',
      );
      expect(dialog().textContent).not.toContain('Check the highlighted');
    },
  );

  it('edits the name only, showing the direction as text and why', async () => {
    const api = stub({
      [typePatch(TYPE_DEDUCTION.id)]: () => jsonResponse(200, TYPE_DEDUCTION),
    });
    await openTypes();
    await click(named(typeRow(TYPE_DEDUCTION), `Edit ${TYPE_DEDUCTION.name}`)!);
    await settleUntil(() => openDialog());
    expect(dialog().querySelector('select')).toBeNull();
    expect(dialog().textContent).toContain(
      'A wrong direction needs a new type',
    );
    expect(
      dialog().querySelector('[data-pay-type-direction]')?.textContent,
    ).toContain('Deduction');

    setFieldValue(labelled(dialog(), 'Name'), 'Anticipo');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    const route = typePatch(TYPE_DEDUCTION.id);
    expect(posted(api, route)[0]!.body).toEqual({
      version: 1,
      name: 'Anticipo',
    });
  });

  it('on a version conflict refreshes, resets the form and says so', async () => {
    const newer = { ...TYPE_BONUS, name: 'Bono nuevo', version: 2 };
    const reads = [
      () => jsonResponse(200, PAY_ITEM_TYPES),
      () => jsonResponse(200, [newer]),
    ];
    const api = stub({
      [TYPES]: reads,
      [typePatch(TYPE_BONUS.id)]: () => problem(409, 'version_conflict'),
    });
    await openTypes();
    await click(named(typeRow(TYPE_BONUS), `Edit ${TYPE_BONUS.name}`)!);
    await settleUntil(() => openDialog());
    setFieldValue(labelled(dialog(), 'Name'), 'Mío');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => dialog().querySelector('[data-pay-type-reloaded]'));
    expect(dialog().textContent).toContain('Someone else changed this type');
    expect(labelled<HTMLInputElement>(dialog(), 'Name').value).toBe(
      'Bono nuevo',
    );
    expect(posted(api, typePatch(TYPE_BONUS.id))).toHaveLength(1);
  });

  it('deactivates behind a confirmation', async () => {
    const api = stub({
      [typePatch(TYPE_BONUS.id)]: () => jsonResponse(200, TYPE_BONUS),
    });
    await openTypes();
    await click(named(typeRow(TYPE_BONUS), `Deactivate ${TYPE_BONUS.name}`)!);
    await settleUntil(() => openDialog());
    expect(posted(api, typePatch(TYPE_BONUS.id))).toHaveLength(0);
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => !openDialog());
    expect(posted(api, typePatch(TYPE_BONUS.id))[0]!.body).toEqual({
      version: 1,
      is_active: false,
    });
    expect(statusText()).toContain('Type deactivated.');
  });

  it('reactivates an inactive type', async () => {
    const api = stub({
      [typePatch(TYPE_RETIRED.id)]: () => jsonResponse(200, TYPE_RETIRED),
    });
    await openTypes();
    await click(
      named(typeRow(TYPE_RETIRED), `Reactivate ${TYPE_RETIRED.name}`)!,
    );
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Reactivate'));
    await settleUntil(() => !openDialog());
    expect(posted(api, typePatch(TYPE_RETIRED.id))[0]!.body).toEqual({
      version: 1,
      is_active: true,
    });
  });

  it('keeps the confirmation open on pay_item_type_in_use', async () => {
    stub({
      [typePatch(TYPE_BONUS.id)]: () => problem(409, 'pay_item_type_in_use'),
    });
    await openTypes();
    await click(named(typeRow(TYPE_BONUS), `Deactivate ${TYPE_BONUS.name}`)!);
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => dialog().textContent?.includes('End them first'));
    expect(openDialog()).not.toBeNull();
  });

  it('closes a stale confirmation, refreshes and says so', async () => {
    const api = stub({
      [typePatch(TYPE_BONUS.id)]: () => problem(409, 'version_conflict'),
    });
    await openTypes();
    await click(named(typeRow(TYPE_BONUS), `Deactivate ${TYPE_BONUS.name}`)!);
    await settleUntil(() => openDialog());
    const reads = posted(api, TYPES).length;
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => !openDialog());
    expect(statusText()).toContain('Someone else changed this type');
    expect(posted(api, TYPES).length).toBeGreaterThan(reads);
  });
});

describe('items section (B)', () => {
  it('shows every field, the deduction with a minus sign and in words', async () => {
    stub();
    await openPage();
    const paid = row(ITEM_PAID);
    expect(paid.textContent).toContain('Deduction');
    expect(paid.querySelector('[data-pay-item-amount]')?.textContent).toBe(
      '-$1,250.50',
    );
    const bonus = row(ITEM_WAITING);
    expect(bonus.textContent).toContain('Bonus');
    expect(bonus.querySelector('[data-pay-item-amount]')?.textContent).toBe(
      '$500.00',
    );
    expect(bonus.textContent).toContain('Sin faltas en el mes');
    expect(bonus.textContent).toContain('One time');
    expect(row(ITEM_RECURRING).textContent).toContain('Recurring');
  });

  it('comes after the rates, outside the shared compensation view', async () => {
    stub();
    await openPage();
    const rates = [...main().querySelectorAll('h2')].find(
      (h2) => h2.textContent === 'Rates',
    )!;
    expect(
      rates.compareDocumentPosition(section()) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it.each([
    [
      'one-time, no period: waiting, visibly',
      ITEM_WAITING,
      'No open period yet',
    ],
    ['one-time, placed', ITEM_PLACED, 'Open'],
    ['one-time, prepared, shown plainly', ITEM_PREPARED, 'Prepared'],
    ['one-time, applied', ITEM_PAID, 'Paid'],
    ['recurring, open end', ITEM_RECURRING, 'Every period from'],
    ['recurring, a range', ITEM_ENDED, 'Every period from'],
  ] as const)('B2: %s', async (_w, item, text) => {
    stub();
    await openPage();
    const cell = row(item).querySelector('[data-pay-item-lands]')!;
    expect(cell.textContent).toContain(text);
  });

  it('says Paid rather than a period once applied', async () => {
    stub({}, { items: [{ ...ITEM_PLACED, applied: true }] });
    await openPage();
    expect(
      row(ITEM_PLACED).querySelector('[data-pay-item-lands]')?.textContent,
    ).toBe('Paid');
  });

  it('names the placed period by its dates', async () => {
    stub();
    await openPage();
    const text = row(ITEM_PLACED).querySelector(
      '[data-pay-item-lands]',
    )?.textContent;
    expect(text).toMatch(/Oct.*1.*–.*Oct.*31/i);
  });

  it('shows the empty state, and works for a tutor with no terms', async () => {
    stub({}, { items: [] });
    await openPage();
    expect(section().querySelector('[data-pay-items-empty]')).not.toBeNull();
    expect(named(section(), 'Add bonus or deduction')).toBeDefined();
  });

  it('says changes reach a period at its next recalculation (B8)', async () => {
    stub();
    await openPage();
    expect(section().textContent).toContain(
      'Changes reach a period at its next recalculation',
    );
  });

  it('shows the error with a retry, and the rest of the page stays', async () => {
    stub({ [ITEMS]: () => problem(403, 'permission_denied') });
    await openPage();
    await settleUntil(() => alerts(section()).length > 0);
    expect(main().textContent).toContain('Rates');
  });

  it('loads the next page by offset', async () => {
    const searches: string[] = [];
    const page = (response: PayItemPage) => (request: Request) => {
      searches.push(new URL(request.url).search);
      return jsonResponse(200, response);
    };
    stub({
      [ITEMS]: [
        page(payItemPage([ITEM_WAITING], 2)),
        page(payItemPage([ITEM_PLACED], 2, 1)),
      ],
    });
    await openPage();
    await settleUntil(() => row(ITEM_WAITING));
    await click(named(section(), 'Load more')!);
    await settleUntil(() => row(ITEM_PLACED));
    expect(row(ITEM_WAITING)).not.toBeNull();
    expect(named(section(), 'Load more')).toBeUndefined();
    expect(searches).toEqual(['?limit=25&offset=0', '?limit=25&offset=1']);
  });

  it('reads no controls for a viewer', async () => {
    stub({}, { as: VIEWER });
    await openPage();
    await settleUntil(() => row(ITEM_RECURRING));
    expect(section().querySelectorAll('button')).toHaveLength(0);
  });

  it('is read-only on a non-Owner own page, under the note', async () => {
    const api = stub({}, { as: OWN_PAY });
    await openPage();
    await settleUntil(() => row(ITEM_RECURRING));
    expect(section().querySelectorAll('button')).toHaveLength(0);
    expect(main().textContent).toContain('Someone else must change your pay');
    expect(posted(api, TYPES)).toHaveLength(0);
  });

  it('keeps the controls on an Owner own page', async () => {
    stub({}, { as: OWNER_TUTOR });
    await openPage();
    await settleUntil(() => row(ITEM_RECURRING));
    expect(named(section(), 'Add bonus or deduction')).toBeDefined();
  });

  it('offers no delete on an applied item', async () => {
    stub();
    await openPage();
    expect(named(row(ITEM_PAID), 'Delete')).toBeUndefined();
    expect(row(ITEM_ENDED).querySelectorAll('button')).toHaveLength(1);
    expect(row(ITEM_WAITING).querySelectorAll('button')).toHaveLength(1);
  });

  it('offers End only on a recurring item, and no edit anywhere', async () => {
    stub();
    await openPage();
    expect(row(ITEM_WAITING).textContent).not.toContain('End');
    expect(row(ITEM_RECURRING).textContent).toContain('End');
    expect(row(ITEM_ENDED).textContent).toContain('Change end');
    expect(section().textContent).not.toContain('Edit');
  });

  it('moves the keyboard to the section from the #pay-items link', async () => {
    stub();
    app = await renderApp(`/payroll/tutors/${TUTOR_ID}#pay-items`);
    await settleUntil(() => section());
    await settle();
    expect(document.activeElement?.textContent).toBe('Bonuses and deductions');
  });

  it('leaves the focus alone with no hash', async () => {
    stub();
    await openPage();
    expect(document.activeElement?.textContent).not.toBe(
      'Bonuses and deductions',
    );
  });
});

describe('add dialog (B3, B4)', () => {
  it('offers active types only, and loads them only once it opens', async () => {
    const api = stub();
    await openPage();
    expect(posted(api, TYPES)).toHaveLength(0);
    await click(named(section(), 'Add bonus or deduction')!);
    await settleUntil(() => openDialog());
    await settleUntil(
      () => labelled<HTMLSelectElement>(dialog(), 'Type').options.length > 1,
    );
    const options = [...labelled<HTMLSelectElement>(dialog(), 'Type').options];
    expect(options.map((option) => option.value)).toEqual([
      '',
      TYPE_BONUS.id,
      TYPE_DEDUCTION.id,
    ]);
    expect(options[2]!.textContent).toContain('Deduction');
  });

  it('links to the types page with no active type', async () => {
    stub({}, { types: [TYPE_RETIRED] });
    await openAdd();
    const note = dialog().querySelector('[data-pay-item-no-types]')!;
    expect(note.querySelector('a')?.getAttribute('href')).toBe(
      '/payroll/pay-item-types',
    );
  });

  it('warns the tutor will read the description', async () => {
    stub();
    await openAdd();
    expect(dialog().textContent).toContain(
      'The tutor will see this description on their payslip',
    );
  });

  it('shows the end date and the per-period warning only when recurring', async () => {
    stub();
    await openAdd();
    expect(dialog().textContent).not.toContain('Ends on (optional)');
    setFieldValue(labelled(dialog(), 'Recurrence'), 'recurring');
    expect(dialog().textContent).toContain('Ends on (optional)');
    expect(dialog().textContent).toContain(
      'paid in full in every period the dates touch, weekly tutors included',
    );
  });

  it('stops an empty form with no request', async () => {
    const api = stub();
    await openAdd();
    await click(buttonNamed(dialog(), 'Add'));
    expect(dialog().textContent).toContain('Choose a type.');
    expect(dialog().textContent).toContain('Enter the description.');
    expect(dialog().textContent).toContain('Enter the amount.');
    expect(posted(api, ITEM_POST)).toHaveLength(0);
  });

  it('adds, and refreshes the items and nothing else (B8)', async () => {
    const api = stub({
      [ITEM_POST]: () => jsonResponse(201, ITEM_WAITING),
    });
    await openAdd();
    fillOneTime();
    const items = posted(api, ITEMS).length;
    const pay = posted(api, COMPENSATION).length;
    const types = posted(api, TYPES).length;
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => !openDialog());
    expect(posted(api, ITEM_POST)[0]!.body).toEqual({
      pay_item_type_id: TYPE_BONUS.id,
      description: 'Sin faltas',
      amount: '500',
      recurrence: 'one_time',
      effective_from: HOURLY.today,
    });
    expect(posted(api, ITEMS).length).toBe(items + 1);
    expect(posted(api, COMPENSATION)).toHaveLength(pay);
    expect(posted(api, TYPES)).toHaveLength(types);
    expect(statusText()).toContain('Item added.');
  });

  it('reuses the key on a retry and takes a new one on a new opening', async () => {
    const api = stub({
      [ITEM_POST]: [
        () => problem(400, 'database_unavailable'),
        () => jsonResponse(201, ITEM_WAITING),
        () => jsonResponse(201, ITEM_PLACED),
      ],
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => alerts(dialog()).length > 0);
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => !openDialog());

    await click(named(section(), 'Add bonus or deduction')!);
    await settleUntil(() => openDialog());
    await settle();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => !openDialog());

    const keys = posted(api, ITEM_POST).map((request) =>
      request.headers.get('Idempotency-Key'),
    );
    expect(keys).toHaveLength(3);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it('takes a new key on a new opening after a refused attempt too', async () => {
    const api = stub({
      [ITEM_POST]: () => problem(400, 'database_unavailable'),
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => alerts(dialog()).length > 0);
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => !openDialog());

    await click(named(section(), 'Add bonus or deduction')!);
    await settleUntil(() => openDialog());
    await settle();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => posted(api, ITEM_POST).length === 2);
    const keys = posted(api, ITEM_POST).map((request) =>
      request.headers.get('Idempotency-Key'),
    );
    expect(keys[1]).not.toBe(keys[0]);
  });

  it.each([
    ['Cancel', () => click(buttonNamed(dialog(), 'Cancel'))],
    ['Escape', () => pressEscape()],
  ] as const)(
    'after a lost response, %s shows the stored item (QA D2)',
    async (_how, close) => {
      const lost = () => Promise.reject(new TypeError('Failed to fetch'));
      const api = stub({
        [ITEMS]: [
          () => jsonResponse(200, payItemPage([])),
          () => jsonResponse(200, payItemPage([ITEM_WAITING])),
        ],
        [ITEM_POST]: lost,
      });
      await openAdd();
      fillOneTime();
      await click(buttonNamed(dialog(), 'Add'));
      await settleUntil(() => alerts(dialog()).length > 0);
      expect(
        dialog().querySelector('[data-pay-item-unknown]')?.textContent,
      ).toContain('Sending it again from here unchanged');
      expect(
        dialog()
          .querySelector('[data-pay-item-unknown]')
          ?.closest('[role="alert"]'),
      ).not.toBeNull();
      expect(posted(api, ITEMS)).toHaveLength(1);

      await close();
      await settleUntil(() => !openDialog());
      await settleUntil(() => row(ITEM_WAITING));
      expect(posted(api, ITEMS)).toHaveLength(2);
    },
  );

  it.each([
    [
      'an aborted fetch',
      () => Promise.reject(new TypeError('Failed to fetch')),
    ],
    ['a 502', () => problem(502, 'internal_error')],
  ] as const)(
    'when the refresh fails with %s, says so; the retry shows the item (D4)',
    async (_how, failure) => {
      let down = false;
      const api: ApiStub = stub({
        [ITEMS]: () =>
          down
            ? failure()
            : jsonResponse(
                200,
                payItemPage(api.calls(ITEM_POST).length ? [ITEM_WAITING] : []),
              ),
        [ITEM_POST]: () => {
          down = true;
          return Promise.reject(new TypeError('Failed to fetch'));
        },
      });
      await openAdd();
      fillOneTime();
      await click(buttonNamed(dialog(), 'Add'));
      await settleUntil(() => alerts(dialog()).length > 0);
      await click(buttonNamed(dialog(), 'Cancel'));
      await settleUntil(() => !openDialog());

      await settleUntil(() => named(section(), 'Reload the list'));
      expect(alerts(section()).join(' ')).toContain('may be out of date');
      expect(row(ITEM_WAITING)).toBeNull();

      down = false;
      await click(named(section(), 'Reload the list')!);
      await settleUntil(() => row(ITEM_WAITING));
      await settleUntil(() => !named(section(), 'Reload the list'));
      expect(alerts(section())).toEqual([]);
    },
  );

  it('keeps the notice when the retry fails too', async () => {
    let down = false;
    const api = stub({
      [ITEMS]: () =>
        down
          ? problem(400, 'database_unavailable')
          : jsonResponse(200, payItemPage([])),
      [ITEM_POST]: () => {
        down = true;
        return Promise.reject(new TypeError('Failed to fetch'));
      },
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => alerts(dialog()).length > 0);
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => named(section(), 'Reload the list'));
    const reads = posted(api, ITEMS).length;
    await click(named(section(), 'Reload the list')!);
    await settleUntil(() => posted(api, ITEMS).length > reads);
    await settle();
    expect(named(section(), 'Reload the list')).toBeDefined();
  });

  it('does not refresh on a plain close', async () => {
    const api = stub();
    await openAdd();
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => !openDialog());
    await settle();
    expect(posted(api, ITEMS)).toHaveLength(1);
  });

  it('says nothing about a stored item after a plain refusal', async () => {
    stub({ [ITEM_POST]: () => problem(403, 'payroll_own_compensation') });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(dialog().querySelector('[data-pay-item-unknown]')).toBeNull();
  });

  it('takes a new key when the body changes', async () => {
    const api = stub({
      [ITEM_POST]: [
        () => problem(400, 'database_unavailable'),
        () => jsonResponse(201, ITEM_WAITING),
      ],
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => alerts(dialog()).length > 0);
    setFieldValue(labelled(dialog(), 'Amount (MXN)'), '600');
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => !openDialog());
    const keys = posted(api, ITEM_POST).map((request) =>
      request.headers.get('Idempotency-Key'),
    );
    expect(keys[1]).not.toBe(keys[0]);
  });

  it('on pay_item_type_inactive says so on the field and drops the type', async () => {
    const api = stub({
      [ITEM_POST]: () => problem(409, 'pay_item_type_inactive'),
      [TYPES]: [
        () => jsonResponse(200, PAY_ITEM_TYPES),
        () =>
          jsonResponse(200, [
            { ...TYPE_BONUS, is_active: false },
            TYPE_DEDUCTION,
          ]),
      ],
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(
      () => labelled<HTMLSelectElement>(dialog(), 'Type').options.length === 2,
    );
    await settle();
    const select = labelled<HTMLSelectElement>(dialog(), 'Type');
    expect(select.value).toBe('');
    expect(dialog().textContent).toContain('This type is inactive');
    expect(select.getAttribute('aria-invalid')).toBe('true');
    expect(labelled<HTMLTextAreaElement>(dialog(), 'Description').value).toBe(
      '  Sin faltas  ',
    );
    // Nothing goes out with no type.
    await click(buttonNamed(dialog(), 'Add'));
    expect(posted(api, ITEM_POST)).toHaveLength(1);
  });

  it('with the last type gone, keeps the refusal and links to the types', async () => {
    stub({
      [ITEM_POST]: () => problem(409, 'pay_item_type_inactive'),
      [TYPES]: [
        () => jsonResponse(200, [TYPE_BONUS]),
        () => jsonResponse(200, [{ ...TYPE_BONUS, is_active: false }]),
      ],
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => dialog().querySelector('[data-pay-item-no-types]'));
    expect(dialog().textContent).toContain('This type is inactive');
  });

  it.each([
    ['amount', 'money_out_of_range', 'Amount (MXN)', 'The amount is too large'],
    ['description', 'text_too_long', 'Description', 'This text is too long'],
    ['description', 'text_invalid', 'Description', 'invalid character'],
    ['effective_from', 'date_out_of_range', 'Applies from', 'this century'],
  ])('puts a refused %s (%s) on its field', async (key, code, label, text) => {
    stub({
      [ITEM_POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', key], code, message: 'x' }],
        }),
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(
      () => labelled(dialog(), label).getAttribute('aria-invalid') === 'true',
    );
    expect(dialog().textContent).toContain(text);
  });

  it('puts text_invalid on the whole body on the description (#109 D11)', async () => {
    stub({
      [ITEM_POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body'], code: 'text_invalid', message: 'x' }],
        }),
    });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(
      () =>
        labelled(dialog(), 'Description').getAttribute('aria-invalid') ===
        'true',
    );
    expect(dialog().textContent).toContain('invalid character');
  });

  it('shows any other refusal as an alert, and stays open', async () => {
    stub({ [ITEM_POST]: () => problem(403, 'payroll_own_compensation') });
    await openAdd();
    fillOneTime();
    await click(buttonNamed(dialog(), 'Add'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(dialog().textContent).toContain('Someone else must change your pay');
  });

  it('returns the focus to Add after closing', async () => {
    stub();
    await openPage();
    const add = named(section(), 'Add bonus or deduction')!;
    act(() => add.focus());
    await click(add);
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => !openDialog());
    await settle();
    expect(document.activeElement).toBe(add);
  });
});

describe('end dialog (B5)', () => {
  const ROUTE = itemRoute('PATCH', ITEM_RECURRING.id);
  const endButton = () =>
    [...row(ITEM_RECURRING).querySelectorAll('button')].find(
      (button) => button.textContent === 'End',
    )!;
  const open = async () => {
    await openPage();
    await click(endButton());
    await settleUntil(() => openDialog());
  };

  it('saves the date with the version, and refreshes only the items', async () => {
    const api = stub({ [ROUTE]: () => jsonResponse(200, ITEM_RECURRING) });
    await open();
    setFieldValue(labelled(dialog(), 'Last day'), '2026-11-30');
    const pay = posted(api, COMPENSATION).length;
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() => !openDialog());
    expect(posted(api, ROUTE)[0]!.body).toEqual({
      version: 1,
      effective_to: '2026-11-30',
    });
    expect(posted(api, COMPENSATION)).toHaveLength(pay);
    expect(statusText()).toContain('End date saved.');
  });

  it('starts at today, within the item', async () => {
    stub();
    await open();
    expect(labelled<HTMLInputElement>(dialog(), 'Last day').value).toBe(
      HOURLY.today,
    );
    expect(labelled<HTMLInputElement>(dialog(), 'Last day').min).toBe(
      ITEM_RECURRING.effective_from,
    );
  });

  it('shows pay_item_end_before_paid_period on the field', async () => {
    stub({ [ROUTE]: () => problem(422, 'pay_item_end_before_paid_period') });
    await open();
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() =>
      dialog().textContent?.includes('already paid in a finalized period'),
    );
    expect(labelled(dialog(), 'Last day').getAttribute('aria-invalid')).toBe(
      'true',
    );
  });

  it('shows pay_item_type_inactive as an alert, and stays open', async () => {
    stub({ [ROUTE]: () => problem(409, 'pay_item_type_inactive') });
    await open();
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(dialog().textContent).toContain('This type is inactive');
  });

  it('on a version conflict reloads and says so', async () => {
    const newer = { ...ITEM_RECURRING, version: 2, effective_to: '2026-12-15' };
    stub({
      [ITEMS]: [
        () => jsonResponse(200, payItemPage(PAY_ITEMS)),
        () =>
          jsonResponse(
            200,
            payItemPage(
              PAY_ITEMS.map((item) => (item.id === newer.id ? newer : item)),
            ),
          ),
      ],
      [ROUTE]: () => problem(409, 'version_conflict'),
    });
    await open();
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() => dialog().querySelector('[data-pay-item-reloaded]'));
    expect(labelled<HTMLInputElement>(dialog(), 'Last day').value).toBe(
      '2026-12-15',
    );
  });

  it('refreshes when the item is gone', async () => {
    const api = stub({ [ROUTE]: () => problem(404, 'not_found') });
    await open();
    const reads = posted(api, ITEMS).length;
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() => !openDialog());
    expect(statusText()).toContain('That item no longer exists');
    expect(posted(api, ITEMS).length).toBeGreaterThan(reads);
  });

  it('does not say the list reloaded when it did not (D4)', async () => {
    let down = false;
    stub({
      [ITEMS]: () =>
        down
          ? problem(400, 'database_unavailable')
          : jsonResponse(200, payItemPage(PAY_ITEMS)),
      [ROUTE]: () => {
        down = true;
        return problem(404, 'not_found');
      },
    });
    await open();
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() => !openDialog());
    await settleUntil(() => statusText().includes("couldn't be reloaded"));
    expect(statusText()).not.toContain('was reloaded');
    expect(named(section(), 'Reload the list')).toBeDefined();
  });

  it('moves the focus to the heading when the item is gone (QA D3)', async () => {
    stub({
      [ITEMS]: [
        () => jsonResponse(200, payItemPage(PAY_ITEMS)),
        () =>
          jsonResponse(
            200,
            payItemPage(
              PAY_ITEMS.filter((item) => item.id !== ITEM_RECURRING.id),
            ),
          ),
      ],
      [ROUTE]: () => problem(404, 'not_found'),
    });
    await open();
    await click(buttonNamed(dialog(), 'Save end'));
    await settleUntil(() => !openDialog());
    await settleUntil(() => !row(ITEM_RECURRING));
    await settle();
    expect(document.activeElement?.textContent).toBe('Bonuses and deductions');
  });
});

describe('delete (B6)', () => {
  const ROUTE = itemRoute('DELETE', ITEM_WAITING.id);
  const open = async () => {
    await openPage();
    await click(
      [...row(ITEM_WAITING).querySelectorAll('button')].find(
        (button) => button.textContent === 'Delete',
      )!,
    );
    await settleUntil(() => openDialog());
  };

  it('deletes behind a confirmation, and refreshes only the items', async () => {
    const api = stub({ [ROUTE]: () => noContent() });
    await open();
    expect(posted(api, ROUTE)).toHaveLength(0);
    const pay = posted(api, COMPENSATION).length;
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
    expect(posted(api, ROUTE)).toHaveLength(1);
    expect(posted(api, COMPENSATION)).toHaveLength(pay);
    expect(statusText()).toContain('Item deleted.');
    await settle();
    expect(document.activeElement?.textContent).toBe('Bonuses and deductions');
  });

  it('shows a deduction with its sign and word, as the list does', async () => {
    stub();
    await openPage();
    await click(
      [...row(ITEM_RECURRING).querySelectorAll('button')].find(
        (button) => button.textContent === 'Delete',
      )!,
    );
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain('-$300.00');
    expect(dialog().textContent).toContain('(Deduction)');
  });

  it('shows a bonus with no sign, and its word', async () => {
    stub();
    await open();
    expect(dialog().textContent).toContain('$500.00');
    expect(dialog().textContent).not.toContain('-$');
    expect(dialog().textContent).toContain('(Bonus)');
  });

  it('stays open with the message on pay_item_in_use', async () => {
    stub({ [ROUTE]: () => problem(409, 'pay_item_in_use') });
    await open();
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() =>
      dialog().textContent?.includes('The period must be returned first'),
    );
    expect(openDialog()).not.toBeNull();
  });

  it('refreshes when the item is gone', async () => {
    const api = stub({ [ROUTE]: () => problem(404, 'not_found') });
    await open();
    const reads = posted(api, ITEMS).length;
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
    expect(statusText()).toContain('That item no longer exists');
    expect(posted(api, ITEMS).length).toBeGreaterThan(reads);
  });

  it('does not say the list reloaded when it did not (D4)', async () => {
    let down = false;
    stub({
      [ITEMS]: () =>
        down
          ? problem(400, 'database_unavailable')
          : jsonResponse(200, payItemPage(PAY_ITEMS)),
      [ROUTE]: () => {
        down = true;
        return problem(404, 'not_found');
      },
    });
    await open();
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
    await settleUntil(() => statusText().includes("couldn't be reloaded"));
    expect(named(section(), 'Reload the list')).toBeDefined();
  });
});

describe('language and display (D)', () => {
  it('reads in Spanish, with Bono and Deducción', async () => {
    applyLanguage('es');
    stub();
    await openPage();
    expect(row(ITEM_PAID).textContent).toContain('Deducción');
    expect(row(ITEM_WAITING).textContent).toContain('Bono');
    expect(row(ITEM_WAITING).textContent).toContain(
      'Aún no hay periodo abierto',
    );
  });

  it('keeps the long description in the row, without widening it', async () => {
    stub({}, { items: [ITEM_LONG] });
    await openPage();
    await settleUntil(() => row(ITEM_LONG));
    const cell = [...row(ITEM_LONG).querySelectorAll('td')].find((td) =>
      td.textContent?.startsWith('Descripción-sin'),
    );
    expect(cell?.className).toContain('overflow-wrap:anywhere');
  });

  it('renders each amount in the cards too', async () => {
    stub();
    await openPage();
    expect(
      section().querySelector(
        `[data-pay-item-card="${ITEM_PAID.id}"] [data-pay-item-amount]`,
      )?.textContent,
    ).toBe('-$1,250.50');
  });
});
