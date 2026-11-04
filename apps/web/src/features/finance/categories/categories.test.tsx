import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
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
import type { ExpenseCategory } from '../api';
import {
  CAT_CUSTOM,
  CAT_RENAMED,
  CAT_RENT,
  CAT_RETIRED,
  MIXED_CATEGORIES,
  SEEDED_CATEGORIES,
} from '../fixtures';
import { validateCategoryForm } from '../forms';
import { CATEGORIES, CATEGORY_POST, categoryPatch, FINANCE } from '../testing';

// M7-11b B, D, E: the categories page. Seeded labels in both languages, a
// renamed row's own text, the badges and the sort, each dialog's success and
// refusal, the keyed-row warning, stale confirmations, and the #458 rules
// (refetch on close after a failed save, the stale alert, never "reloaded"
// unless it did) plus D2's lost create.

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const dialog = () => openDialog()!;
const tableRows = () =>
  [...main().querySelectorAll('[data-categories-table] tbody tr')].map(
    (tr) => tr.querySelector('td')?.textContent,
  );
const row = (category: ExpenseCategory) =>
  main().querySelector<HTMLElement>(
    `[data-categories-table] [data-category-id="${category.id}"]`,
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

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    as = FINANCE,
    categories = MIXED_CATEGORIES,
  }: { as?: SessionUserBody; categories?: ExpenseCategory[] } = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [CATEGORIES]: () => jsonResponse(200, categories),
    ...routes,
  });
}

async function openPage() {
  app = await renderApp('/expenses/categories');
  await settleUntil(() => main().querySelector('h1'));
  await settle();
}

async function openAdd() {
  await click(named(main(), 'Add category')!);
  await settleUntil(() => openDialog());
}

async function openRename(category: ExpenseCategory, label: string) {
  await click(named(row(category), `Rename ${label}`)!);
  await settleUntil(() => openDialog());
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
  it('needs a name of at most 100 code points', () => {
    expect(validateCategoryForm({ name: '   ' })).toEqual({
      name: 'finance.validation.nameRequired',
    });
    expect(validateCategoryForm({ name: '😀'.repeat(100) })).toEqual({});
    expect(validateCategoryForm({ name: 'a'.repeat(101) })).toEqual({
      name: 'finance.validation.categoryNameTooLong',
    });
  });
});

describe('list (B1)', () => {
  it('shows the seeded labels in English, sorted by label', async () => {
    stub({}, { categories: SEEDED_CATEGORIES });
    await openPage();
    expect(tableRows()).toEqual([
      'Equipment',
      'Maintenance',
      'Rent',
      'Supplies',
      'Utilities',
    ]);
    expect(
      main().querySelectorAll(
        '[data-categories-table] [data-slot="finance-default-badge"]',
      ),
    ).toHaveLength(5);
  });

  it('shows the seeded labels in Spanish, sorted by label', async () => {
    applyLanguage('es');
    stub({}, { categories: SEEDED_CATEGORIES });
    await openPage();
    expect(tableRows()).toEqual([
      'Equipo',
      'Insumos',
      'Mantenimiento',
      'Renta',
      'Servicios',
    ]);
    expect(main().textContent).toContain('Predefinida');
  });

  it('shows a renamed row as stored, inactive rows last, with badges', async () => {
    stub();
    await openPage();
    expect(tableRows()).toEqual([
      'Equipment',
      'Luz y agua',
      'Maintenance',
      'Publicidad',
      'Rent',
      'Supplies',
      'Cafetería',
    ]);
    expect(row(CAT_RENAMED).textContent).not.toContain('Default');
    expect(row(CAT_RENT).textContent).toContain('Default');
    expect(row(CAT_RENT).textContent).toContain('Active');
    expect(row(CAT_RETIRED).textContent).toContain('Inactive');
    expect(named(row(CAT_RETIRED), 'Reactivate Cafetería')).toBeDefined();
  });

  it('changes keyed labels, not renamed ones, with the language', async () => {
    stub();
    await openPage();
    applyLanguage('es');
    await settle();
    expect(row(CAT_RENT).querySelector('td')?.textContent).toBe('Renta');
    expect(row(CAT_RENAMED).querySelector('td')?.textContent).toBe(
      'Luz y agua',
    );
  });

  it('shows the cards too, for small screens', async () => {
    stub();
    await openPage();
    expect(
      main().querySelectorAll('[data-categories-cards] [data-category-card]'),
    ).toHaveLength(MIXED_CATEGORIES.length);
  });

  it('shows the empty state', async () => {
    stub({}, { categories: [] });
    await openPage();
    expect(main().querySelector('[data-categories-empty]')?.textContent).toBe(
      'There are no categories yet.',
    );
  });

  it('shows the error with a retry', async () => {
    stub({ [CATEGORIES]: () => problem(400, 'database_unavailable') });
    await openPage();
    await settleUntil(() => alerts(main()).length > 0);
    expect(named(main(), 'Try again')).toBeDefined();
  });
});

describe('add dialog (B2)', () => {
  it('adds a trimmed name and refreshes', async () => {
    const api = stub({
      [CATEGORY_POST]: () => jsonResponse(201, CAT_CUSTOM),
    });
    await openPage();
    await openAdd();
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Enter the name.');
    expect(api.calls(CATEGORY_POST)).toHaveLength(0);

    setFieldValue(labelled(dialog(), 'Name'), '  Publicidad  ');
    const reads = api.calls(CATEGORIES).length;
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(CATEGORY_POST)[0]!.body).toEqual({ name: 'Publicidad' });
    expect(api.calls(CATEGORIES).length).toBeGreaterThan(reads);
    expect(statusText()).toContain('Category saved.');
  });

  it('puts expense_category_exists on the name, with the reactivation hint', async () => {
    stub({ [CATEGORY_POST]: () => problem(409, 'expense_category_exists') });
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'Cafetería');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() =>
      dialog().textContent?.includes(
        'A category with this name already exists',
      ),
    );
    expect(labelled(dialog(), 'Name').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(dialog().querySelector('[data-exists-hint]')?.textContent).toContain(
      'A deactivated category with this name may exist',
    );
    expect(dialog().querySelector('[data-row-notice]')).toBeNull();
  });

  it.each([
    ['text_empty', 'Enter some text.'],
    ['text_too_long', 'This text is too long.'],
    ['text_invalid', 'This text has an invalid character.'],
  ])('puts %s on the name', async (code, text) => {
    stub({
      [CATEGORY_POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'name'], code, message: 'x' }],
        }),
    });
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'Algo');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => dialog().textContent?.includes(text));
    expect(labelled(dialog(), 'Name').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(dialog().querySelector('[data-exists-hint]')).toBeNull();
  });
});

describe('rename dialog (B3, B5)', () => {
  it('starts from the label and warns on a keyed row only', async () => {
    stub();
    await openPage();
    await openRename(CAT_RENT, 'Rent');
    expect(labelled<HTMLInputElement>(dialog(), 'Name').value).toBe('Rent');
    expect(dialog().querySelector('[data-keyed-warning]')?.textContent).toBe(
      "This is a default category: the text you type will show in both languages, and the default translation won't come back.",
    );
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => !openDialog());

    await openRename(CAT_RENAMED, 'Luz y agua');
    expect(labelled<HTMLInputElement>(dialog(), 'Name').value).toBe(
      'Luz y agua',
    );
    expect(dialog().querySelector('[data-keyed-warning]')).toBeNull();
  });

  it('sends nothing when the text is unchanged', async () => {
    const api = stub();
    await openPage();
    await openRename(CAT_RENT, 'Rent');
    setFieldValue(labelled(dialog(), 'Name'), ' Rent ');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(categoryPatch(CAT_RENT.id))).toHaveLength(0);
    expect(statusText()).not.toContain('Category saved.');
  });

  it('renames with the version', async () => {
    const api = stub({
      [categoryPatch(CAT_RENT.id)]: () =>
        jsonResponse(200, { ...CAT_RENT, name: 'Alquiler', system_key: null }),
    });
    await openPage();
    await openRename(CAT_RENT, 'Rent');
    setFieldValue(labelled(dialog(), 'Name'), 'Alquiler');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(categoryPatch(CAT_RENT.id))[0]!.body).toEqual({
      version: 1,
      name: 'Alquiler',
    });
    expect(statusText()).toContain('Category saved.');
  });

  it('renames an inactive row too', async () => {
    const api = stub({
      [categoryPatch(CAT_RETIRED.id)]: () => jsonResponse(200, CAT_RETIRED),
    });
    await openPage();
    await openRename(CAT_RETIRED, 'Cafetería');
    setFieldValue(labelled(dialog(), 'Name'), 'Café');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(categoryPatch(CAT_RETIRED.id))[0]!.body).toEqual({
      version: 3,
      name: 'Café',
    });
  });

  it('on version_conflict reloads, resets the form and says so', async () => {
    const newer = { ...CAT_CUSTOM, name: 'Marketing', version: 2 };
    const api = stub({
      [CATEGORIES]: [
        () => jsonResponse(200, MIXED_CATEGORIES),
        () => jsonResponse(200, [newer]),
      ],
      [categoryPatch(CAT_CUSTOM.id)]: () => problem(409, 'version_conflict'),
    });
    await openPage();
    await openRename(CAT_CUSTOM, 'Publicidad');
    setFieldValue(labelled(dialog(), 'Name'), 'Anuncios');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() =>
      dialog().querySelector('[data-row-notice="conflictReloaded"]'),
    );
    expect(dialog().textContent).toContain(
      'Someone else changed this category. It was reloaded',
    );
    expect(labelled<HTMLInputElement>(dialog(), 'Name').value).toBe(
      'Marketing',
    );
    expect(api.calls(categoryPatch(CAT_CUSTOM.id))).toHaveLength(1);
  });

  it('on version_conflict with a failed reload, never says it reloaded', async () => {
    let down = false;
    stub({
      [CATEGORIES]: () =>
        down
          ? problem(400, 'database_unavailable')
          : jsonResponse(200, MIXED_CATEGORIES),
      [categoryPatch(CAT_CUSTOM.id)]: () => {
        down = true;
        return problem(409, 'version_conflict');
      },
    });
    await openPage();
    await openRename(CAT_CUSTOM, 'Publicidad');
    setFieldValue(labelled(dialog(), 'Name'), 'Anuncios');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => named(dialog(), 'Reload'));
    expect(dialog().textContent).not.toContain('reloaded');
    expect(alerts(dialog()).join(' ')).toContain(
      'Someone else changed this record',
    );
  });

  it.each([
    [true, 'This category no longer exists. The list was reloaded.'],
    [false, "This category no longer exists. The list couldn't be reloaded."],
  ])(
    'on 404 (reload works: %s) says so and locks the form',
    async (works, text) => {
      let gone = false;
      stub({
        [CATEGORIES]: () =>
          gone && !works
            ? problem(400, 'database_unavailable')
            : jsonResponse(
                200,
                gone
                  ? MIXED_CATEGORIES.filter((c) => c.id !== CAT_CUSTOM.id)
                  : MIXED_CATEGORIES,
              ),
        [categoryPatch(CAT_CUSTOM.id)]: () => {
          gone = true;
          return problem(404, 'not_found');
        },
      });
      await openPage();
      await openRename(CAT_CUSTOM, 'Publicidad');
      setFieldValue(labelled(dialog(), 'Name'), 'Anuncios');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(() => dialog().textContent?.includes(text));
      expect(buttonNamed(dialog(), 'Save').disabled).toBe(true);
    },
  );
});

describe('deactivate and reactivate (B4, B5)', () => {
  it('deactivates behind the confirmation and announces it', async () => {
    const api = stub({
      [categoryPatch(CAT_RENT.id)]: () =>
        jsonResponse(200, { ...CAT_RENT, is_active: false }),
    });
    await openPage();
    await click(named(row(CAT_RENT), 'Deactivate Rent')!);
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain('Deactivate Rent');
    expect(dialog().textContent).toContain(
      "It can't be chosen for new expenses; past expenses and reports keep it.",
    );
    expect(api.calls(categoryPatch(CAT_RENT.id))).toHaveLength(0);
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => !openDialog());
    expect(api.calls(categoryPatch(CAT_RENT.id))[0]!.body).toEqual({
      version: 1,
      is_active: false,
    });
    const status = main().querySelector('[role="status"]')!;
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toBe('Category deactivated.');
  });

  it('says the deactivation sentence in Spanish', async () => {
    applyLanguage('es');
    stub();
    await openPage();
    await click(named(row(CAT_RENT), 'Desactivar Renta')!);
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain(
      'No se podrá elegir en gastos nuevos; los gastos y reportes anteriores la conservan.',
    );
  });

  it('reactivates an inactive row', async () => {
    const api = stub({
      [categoryPatch(CAT_RETIRED.id)]: () => jsonResponse(200, CAT_RETIRED),
    });
    await openPage();
    await click(named(row(CAT_RETIRED), 'Reactivate Cafetería')!);
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Reactivate'));
    await settleUntil(() => !openDialog());
    expect(api.calls(categoryPatch(CAT_RETIRED.id))[0]!.body).toEqual({
      version: 3,
      is_active: true,
    });
    expect(statusText()).toBe('Category reactivated.');
  });

  it('keeps the confirmation open on any other refusal', async () => {
    stub({
      [categoryPatch(CAT_RENT.id)]: () => problem(403, 'permission_denied'),
    });
    await openPage();
    await click(named(row(CAT_RENT), 'Deactivate Rent')!);
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(openDialog()).not.toBeNull();
  });

  it.each([
    [
      404,
      'not_found',
      'That category no longer exists. The list was reloaded.',
    ],
    [
      409,
      'version_conflict',
      'Someone else changed this category. The list was reloaded',
    ],
  ])(
    'a stale confirmation (%s) closes, refreshes and says so',
    async (status, code, text) => {
      const api = stub({
        [categoryPatch(CAT_RENT.id)]: () => problem(status, code),
      });
      await openPage();
      await click(named(row(CAT_RENT), 'Deactivate Rent')!);
      await settleUntil(() => openDialog());
      const reads = api.calls(CATEGORIES).length;
      await click(buttonNamed(dialog(), 'Deactivate'));
      await settleUntil(() => !openDialog());
      expect(statusText()).toContain(text);
      expect(api.calls(CATEGORIES).length).toBeGreaterThan(reads);
    },
  );
});

describe('lost answers and failed reloads (D1, D2)', () => {
  const NOT_RELOADED = "couldn't be reloaded";

  it.each([
    ['gone', 404, 'not_found', 'That category no longer exists.'],
    ['changed', 409, 'version_conflict', 'Someone else changed this category.'],
  ] as const)(
    'a %s row says the list reloaded only once it did',
    async (_what, status, code, opening) => {
      let down = false;
      stub({
        [CATEGORIES]: () =>
          down
            ? problem(400, 'database_unavailable')
            : jsonResponse(200, MIXED_CATEGORIES),
        [categoryPatch(CAT_RENT.id)]: () => {
          down = true;
          return problem(status, code);
        },
      });
      await openPage();
      await click(named(row(CAT_RENT), 'Deactivate Rent')!);
      await settleUntil(() => openDialog());
      await click(buttonNamed(dialog(), 'Deactivate'));
      await settleUntil(() => statusText().includes(NOT_RELOADED));
      expect(statusText()).toContain(opening);
      expect(statusText()).not.toContain('was reloaded');
      // The old rows stay, under the out-of-date alert.
      expect(row(CAT_RENT)).not.toBeNull();
      expect(alerts(main()).join(' ')).toContain('may be out of date');

      down = false;
      await click(named(main(), 'Reload the list')!);
      await settleUntil(() => !named(main(), 'Reload the list'));
      expect(alerts(main())).toEqual([]);
      expect(statusText()).toContain('The list was reloaded');
      expect(statusText()).not.toContain(NOT_RELOADED);
    },
  );

  it.each([
    ['Cancel', () => click(buttonNamed(dialog(), 'Cancel'))],
    ['Escape', () => pressEscape()],
    ['the close button', () => click(buttonNamed(dialog(), 'Close'))],
  ] as const)(
    'after a lost create, %s refreshes and shows the stored row',
    async (_how, close) => {
      const stored = { ...CAT_CUSTOM, name: 'Viáticos' };
      const api = stub({
        [CATEGORIES]: [
          () => jsonResponse(200, MIXED_CATEGORIES),
          () => jsonResponse(200, [stored, ...MIXED_CATEGORIES]),
        ],
        [CATEGORY_POST]: () => Promise.reject(new TypeError('Failed to fetch')),
      });
      await openPage();
      await openAdd();
      setFieldValue(labelled(dialog(), 'Name'), 'Viáticos');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(() => alerts(dialog()).length > 0);
      expect(api.calls(CATEGORIES)).toHaveLength(1);

      await close();
      await settleUntil(() => !openDialog());
      await settleUntil(() => tableRows().includes('Viáticos'));
      expect(api.calls(CATEGORIES)).toHaveLength(2);
    },
  );

  it('a plain close refreshes nothing', async () => {
    const api = stub();
    await openPage();
    await openAdd();
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => !openDialog());
    await settle();
    expect(api.calls(CATEGORIES)).toHaveLength(1);
  });

  it.each([
    [
      true,
      'Your earlier attempt may already have saved this name. The list was reloaded',
    ],
    [
      false,
      "Your earlier attempt may already have saved this name. The list couldn't be reloaded",
    ],
  ])(
    'a lost create retried into _exists reloads and says the name may be saved (reload works: %s)',
    async (works, text) => {
      let lost = true;
      const api = stub({
        [CATEGORIES]: () =>
          !lost && !works
            ? problem(400, 'database_unavailable')
            : jsonResponse(200, MIXED_CATEGORIES),
        [CATEGORY_POST]: () => {
          if (lost) {
            lost = false;
            return problem(502, 'internal_error');
          }
          return problem(409, 'expense_category_exists');
        },
      });
      await openPage();
      await openAdd();
      setFieldValue(labelled(dialog(), 'Name'), 'Viáticos');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(() => alerts(dialog()).length > 0);
      const reads = api.calls(CATEGORIES).length;
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(() => dialog().textContent?.includes(text));
      expect(api.calls(CATEGORIES).length).toBeGreaterThan(reads);
      expect(labelled(dialog(), 'Name').getAttribute('aria-invalid')).toBe(
        'true',
      );
    },
  );
});
