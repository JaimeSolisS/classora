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
import type { ExpenseTemplate } from '../api';
import {
  CAT_RETIRED,
  CAT_UTILITIES,
  expensePage,
  GENERATED_EXPENSES,
  MIXED_CATEGORIES,
  TEMPLATE_ENDED,
  TEMPLATE_FUTURE,
  TEMPLATE_PAUSED,
  TEMPLATE_RENT,
  VENDOR_PAPER,
  VENDOR_RETIRED,
  vendorPage,
  VENDORS as VENDOR_ROWS,
} from '../fixtures';
import {
  CATEGORIES,
  EXPENSES,
  FINANCE,
  OWNER,
  templateGet,
  templatePatch,
  VENDORS,
} from '../testing';

// M7-13b A3, C, D2-D6: the template detail per role, its generated
// expenses, the edit, pause, resume and end dialogs with their mapped
// refusals, and the #458 config-page close rules.

const VIEWER: SessionUserBody = {
  ...FINANCE,
  permissions: ['expense.configure', 'expense.view'],
};
const lost = () => Promise.reject(new TypeError('Failed to fetch'));

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const head = () => main().querySelector('[data-template-head]')!;
const fieldText = (key: string) =>
  head().querySelector(`[data-template-field="${key}"] dd`)?.textContent;
const dialog = (kind: string) =>
  document.querySelector<HTMLElement>(`[data-template-dialog="${kind}"]`);
const confirmDialog = () =>
  document.querySelector<HTMLElement>('[role="dialog"]');
const said = () =>
  main().querySelector('[data-template-announcement]')?.textContent ?? '';
const action = (name: string) => {
  const group = main().querySelector('[role="group"]');
  return group
    ? ([...group.querySelectorAll('button')].find(
        (button) => button.textContent === name,
      ) ?? null)
    : null;
};
const fieldError = (root: HTMLElement, label: string) => {
  const field = labelled(root, label);
  const ids = field.getAttribute('aria-describedby')?.split(' ') ?? [];
  return ids
    .filter((id) => id.endsWith('-error'))
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join('');
};
let listed: string[] = [];
const generatedPage =
  (items = GENERATED_EXPENSES, total = items.length, offset = 0): Responder =>
  (request) => {
    listed.push(new URL(request.url).search);
    return jsonResponse(200, expensePage(items, { total, offset }));
  };

function stub(
  template: ExpenseTemplate = TEMPLATE_RENT,
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [templateGet(template.id)]: () => jsonResponse(200, template),
    [EXPENSES]: generatedPage(),
    [CATEGORIES]: () => jsonResponse(200, MIXED_CATEGORIES),
    [VENDORS]: () => jsonResponse(200, vendorPage(VENDOR_ROWS)),
    ...routes,
  });
}

async function openDetail(template: ExpenseTemplate = TEMPLATE_RENT) {
  app = await renderApp(`/expenses/templates/${template.id}`);
  await settleUntil(() =>
    main().querySelector(
      '[data-template-head], [data-template-not-found], [role="alert"]',
    ),
  );
  await settleUntil(() =>
    main().querySelector(
      '[data-generated-table], [data-generated-empty], [data-template-not-found]',
    ),
  );
}

async function openEdit() {
  await click(action('Edit')!);
  await settleUntil(() => dialog('edit')?.querySelector('form'));
}

const closeRoutes = [
  ['Cancel', (root: HTMLElement) => click(buttonNamed(root, 'Cancel'))],
  ['Escape', () => pressEscape()],
  [
    'the close button',
    (root: HTMLElement) =>
      click(root.querySelector<HTMLElement>('[data-slot="dialog-close"]')!),
  ],
] as const;

beforeEach(() => {
  applyLanguage('en');
  listed = [];
  vi.useFakeTimers({ toFake: ['Date'] });
  // 12:00 on 2026-10-20 in Mexico City.
  vi.setSystemTime(new Date('2026-10-20T18:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('the detail (C1, C2)', () => {
  it('shows every header field', async () => {
    stub();
    await openDetail();
    expect(main().querySelector('h1')?.textContent).toBe('Renta del local');
    expect(fieldText('category')).toBe('Rent');
    expect(fieldText('vendor')).toBe(VENDOR_PAPER.name);
    expect(fieldText('amount')).toBe('$12,500.00');
    expect(fieldText('frequency')).toBe('Monthly');
    expect(fieldText('startsOn')).toBe('Sep 1, 2026');
    expect(fieldText('endsOn')).toBe('No end');
    expect(fieldText('dueOffset')).toBe('Due 5 days after the period starts');
    expect(fieldText('next')).toBe('Nov 1, 2026');
    expect(fieldText('last')).toBe('Oct 1, 2026');
    expect(fieldText('status')).toBe('Active');
  });

  it('an ended template: its end, no next period, inactive badges, only Edit', async () => {
    stub(TEMPLATE_ENDED);
    await openDetail(TEMPLATE_ENDED);
    expect(fieldText('endsOn')).toBe('Sep 30, 2026');
    expect(fieldText('next')).toBe('—');
    expect(fieldText('category')).toBe(`${CAT_RETIRED.name}Inactive`);
    expect(fieldText('vendor')).toBe(`${VENDOR_RETIRED.name}Inactive`);
    expect(fieldText('dueOffset')).toBe('Due 1 day after the period starts');
    expect(fieldText('status')).toBe('Ended');
    expect(action('Edit')).not.toBeNull();
    for (const name of ['Pause', 'Resume', 'End']) {
      expect(action(name)).toBeNull();
    }
  });

  it('due on the first day; Spanish wording', async () => {
    applyLanguage('es');
    stub(TEMPLATE_PAUSED);
    await openDetail(TEMPLATE_PAUSED);
    expect(fieldText('dueOffset')).toBe(
      'Vence el día en que empieza el periodo',
    );
    expect(fieldText('frequency')).toBe('Semanal');
    expect(fieldText('vendor')).toBe('—');
    expect(fieldText('status')).toBe('Pausada');
    expect(action('Reanudar')).not.toBeNull();
    expect(action('Pausar')).toBeNull();
  });

  it('lists the generated expenses, newest first, each linking to its expense', async () => {
    stub();
    await openDetail();
    expect(listed[0]).toBe(
      `?template_id=${TEMPLATE_RENT.id}&sort=-incurred_on&limit=25&offset=0`,
    );
    const rows = [
      ...main().querySelectorAll('[data-generated-table] tbody tr'),
    ].map((row) => [...row.querySelectorAll('td')].map((td) => td.textContent));
    expect(rows).toEqual([
      ['Oct 1, 2026', '$12,500.00', '$12,500.00', 'Overdue'],
      ['Sep 1, 2026', '$12,500.00', '$0.00', 'Paid'],
    ]);
    const link = main().querySelector('[data-generated-table] tbody a');
    expect(link?.getAttribute('href')).toBe(
      `/expenses/records/${GENERATED_EXPENSES[0]!.id}`,
    );
  });

  it('pages through the generated expenses', async () => {
    stub(TEMPLATE_RENT, { [EXPENSES]: generatedPage(GENERATED_EXPENSES, 30) });
    await openDetail();
    await click(buttonNamed(main(), 'Next'));
    await settleUntil(() => app!.location().endsWith('?page=2'));
    await settleUntil(() => listed.at(-1)?.includes('offset=25') === true);
  });

  it('nothing generated yet: says when the first one is generated', async () => {
    stub(TEMPLATE_FUTURE, { [EXPENSES]: generatedPage([]) });
    await openDetail(TEMPLATE_FUTURE);
    expect(main().querySelector('[data-generated-empty]')?.textContent).toBe(
      'No expense has been generated yet. The first one is generated on Nov 15, 2026.',
    );
    expect(fieldText('last')).toBe('—');
  });

  it('a 404 or a bad id shows the not-found state with a link back', async () => {
    stub(TEMPLATE_RENT, {
      [templateGet(TEMPLATE_RENT.id)]: () => problem(404, 'not_found'),
    });
    await openDetail();
    expect(
      main().querySelector('[data-template-not-found]')?.textContent,
    ).toContain("This recurring expense doesn't exist.");
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to recurring expenses',
    );
    expect(back?.getAttribute('href')).toBe('/expenses/templates');
    app!.unmount();
    const api = stub();
    app = await renderApp('/expenses/templates/not-a-uuid');
    await settleUntil(() => main().querySelector('[data-template-not-found]'));
    expect(
      api.requests.filter((r) => r.path.includes('expense-templates')),
    ).toEqual([]);
  });

  it('A3: a view-only user sees no action; Finance Staff see them all', async () => {
    stub(TEMPLATE_RENT, {}, VIEWER);
    await openDetail();
    expect(main().querySelector('[role="group"]')).toBeNull();
    app!.unmount();
    stub(TEMPLATE_RENT, {}, FINANCE);
    await openDetail();
    for (const name of ['Edit', 'Pause', 'End']) {
      expect(action(name)).not.toBeNull();
    }
    expect(action('Resume')).toBeNull();
  });
});

describe('Edit (D2, D5, E3)', () => {
  it('shows the fixed fields read-only with the E3 hint and notice', async () => {
    stub();
    await openDetail();
    await openEdit();
    const fixed = dialog('edit')!.querySelector('[data-template-fixed]')!;
    expect(fixed.textContent).toBe(
      'FrequencyMonthlyFirst period starts onSep 1, 2026To change the frequency or the start, end this template and create another.',
    );
    expect(() => labelled(dialog('edit')!, 'Frequency')).toThrow();
    expect(
      dialog('edit')!.querySelector('[data-edit-notice]')?.textContent,
    ).toBe(
      "Changes apply to expenses not generated yet; expenses already generated don't change.",
    );
    // The end can't go before the latest generated period.
    expect(
      labelled<HTMLInputElement>(dialog('edit')!, 'Ends on (optional)').min,
    ).toBe('2026-10-01');
  });

  it('sends only what changed; clearing gives vendor_id and ends_on null', async () => {
    const withEnd = { ...TEMPLATE_RENT, ends_on: '2027-12-31' };
    const saved = {
      ...withEnd,
      vendor: null,
      ends_on: null,
      amount: '13000.00',
      version: 2,
    };
    const api = stub(withEnd, {
      [templatePatch(TEMPLATE_RENT.id)]: () => jsonResponse(200, saved),
    });
    await openDetail(withEnd);
    await openEdit();
    setFieldValue(labelled(dialog('edit')!, 'Vendor (optional)'), '');
    setFieldValue(labelled(dialog('edit')!, 'Ends on (optional)'), '');
    setFieldValue(labelled(dialog('edit')!, 'Amount of each expense'), '13000');
    const before = api.calls(templateGet(TEMPLATE_RENT.id)).length;
    await click(buttonNamed(dialog('edit')!, 'Save'));
    await settleUntil(() => !dialog('edit'));
    expect(api.calls(templatePatch(TEMPLATE_RENT.id))[0]!.body).toEqual({
      version: 1,
      vendor_id: null,
      amount: '13000',
      ends_on: null,
    });
    expect(said()).toBe('Changes saved.');
    // The job moves the periods without a new version: refetch (QA #116 D1).
    await settleUntil(
      () => api.calls(templateGet(TEMPLATE_RENT.id)).length > before,
    );
  });

  it('nothing changed: no request', async () => {
    const api = stub();
    await openDetail();
    await openEdit();
    await click(buttonNamed(dialog('edit')!, 'Save'));
    await settleUntil(() => !dialog('edit'));
    expect(api.calls(templatePatch(TEMPLATE_RENT.id))).toHaveLength(0);
  });

  it('keeps a deactivated category in the picker of a template that uses it', async () => {
    stub(TEMPLATE_ENDED);
    await openDetail(TEMPLATE_ENDED);
    await openEdit();
    const category = labelled<HTMLSelectElement>(dialog('edit')!, 'Category');
    expect(category.value).toBe(CAT_RETIRED.id);
    expect(category.selectedOptions[0]?.textContent).toBe(
      `${CAT_RETIRED.name} (inactive)`,
    );
    const vendor = labelled<HTMLSelectElement>(
      dialog('edit')!,
      'Vendor (optional)',
    );
    expect(vendor.value).toBe(VENDOR_RETIRED.id);
  });

  it('version_conflict reloads inside the dialog and shows the newer version', async () => {
    const newer = { ...TEMPLATE_RENT, name: 'Renta nueva', version: 2 };
    const api = stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () => problem(409, 'version_conflict'),
    });
    await openDetail();
    await openEdit();
    setFieldValue(labelled(dialog('edit')!, 'Amount of each expense'), '1');
    api.set({
      [templateGet(TEMPLATE_RENT.id)]: () => jsonResponse(200, newer),
    });
    await click(buttonNamed(dialog('edit')!, 'Save'));
    await settleUntil(() =>
      dialog('edit')!.textContent.includes('Someone else changed'),
    );
    expect(labelled<HTMLInputElement>(dialog('edit')!, 'Name').value).toBe(
      'Renta nueva',
    );
    expect(
      labelled<HTMLInputElement>(dialog('edit')!, 'Amount of each expense')
        .value,
    ).toBe('12500.00');
  });

  it('a 404 shows the not-found state', async () => {
    const api = stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () => problem(404, 'not_found'),
    });
    await openDetail();
    await openEdit();
    setFieldValue(labelled(dialog('edit')!, 'Amount of each expense'), '1');
    api.set({
      [templateGet(TEMPLATE_RENT.id)]: () => problem(404, 'not_found'),
    });
    await click(buttonNamed(dialog('edit')!, 'Save'));
    await settleUntil(() => main().querySelector('[data-template-not-found]'));
  });

  it.each([
    [
      'expense_template_exists',
      () => problem(409, 'expense_template_exists'),
      'Name',
      'A recurring expense with this name already exists (paused and ended ones count too). Use another name or rename the old one.',
    ],
    [
      'expense_template_ends_on_invalid',
      () =>
        problemWith(422, 'expense_template_ends_on_invalid', {
          errors: [
            {
              path: ['body', 'ends_on'],
              code: 'expense_template_ends_on_invalid',
            },
          ],
        }),
      'Ends on (optional)',
      "The end can't be before the start or before the latest period already generated.",
    ],
    [
      'vendor_inactive',
      () => problem(409, 'vendor_inactive'),
      'Vendor (optional)',
      'This vendor is inactive. Choose another or reactivate it.',
    ],
    [
      'expense_category_inactive',
      () => problem(409, 'expense_category_inactive'),
      'Category',
      'This category is inactive. Choose another or reactivate it.',
    ],
  ])('maps %s onto its field', async (_code, answer, label, text) => {
    stub(TEMPLATE_RENT, { [templatePatch(TEMPLATE_RENT.id)]: answer });
    await openDetail();
    await openEdit();
    setFieldValue(labelled(dialog('edit')!, 'Category'), CAT_UTILITIES.id);
    await click(buttonNamed(dialog('edit')!, 'Save'));
    await settleUntil(() => fieldError(dialog('edit')!, label) !== '');
    expect(fieldError(dialog('edit')!, label)).toBe(text);
  });
});

describe('Pause and Resume (D3)', () => {
  it('pauses with the version', async () => {
    const api = stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () =>
        jsonResponse(200, {
          ...TEMPLATE_RENT,
          is_active: false,
          status: 'paused',
          version: 2,
        }),
    });
    await openDetail();
    await click(action('Pause')!);
    expect(confirmDialog()!.textContent).toContain(
      "No expenses are generated while it's paused.",
    );
    await click(buttonNamed(confirmDialog()!, 'Pause'));
    await settleUntil(() => said() === 'Recurring expense paused.');
    expect(api.calls(templatePatch(TEMPLATE_RENT.id))[0]!.body).toEqual({
      version: 1,
      is_active: false,
    });
    expect(confirmDialog()).toBeNull();
  });

  it('resumes and announces the next period from the answer', async () => {
    const api = stub(TEMPLATE_PAUSED, {
      [templatePatch(TEMPLATE_PAUSED.id)]: () =>
        jsonResponse(200, {
          ...TEMPLATE_PAUSED,
          is_active: true,
          status: 'active',
          next_period_start: '2026-10-26',
          version: 3,
        }),
    });
    await openDetail(TEMPLATE_PAUSED);
    await click(action('Resume')!);
    expect(confirmDialog()!.textContent).toContain(
      "Paused periods aren't generated.",
    );
    await click(buttonNamed(confirmDialog()!, 'Resume'));
    await settleUntil(
      () =>
        said() ===
        'Recurring expense resumed. The next period starts on Oct 26, 2026.',
    );
    expect(api.calls(templatePatch(TEMPLATE_PAUSED.id))[0]!.body).toEqual({
      version: 2,
      is_active: true,
    });
  });

  it('a resume refused for an inactive category stays open with the refusal', async () => {
    stub(TEMPLATE_PAUSED, {
      [templatePatch(TEMPLATE_PAUSED.id)]: () =>
        problem(409, 'expense_category_inactive'),
    });
    await openDetail(TEMPLATE_PAUSED);
    await click(action('Resume')!);
    await click(buttonNamed(confirmDialog()!, 'Resume'));
    await settleUntil(() => alerts(confirmDialog()!).length > 0);
    expect(alerts(confirmDialog()!).join()).toContain(
      'This category is inactive.',
    );
  });

  it('a stale pause (changed) closes, reloads and says so', async () => {
    stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () => problem(409, 'version_conflict'),
    });
    await openDetail();
    await click(action('Pause')!);
    await click(buttonNamed(confirmDialog()!, 'Pause'));
    await settleUntil(() => confirmDialog() === null);
    await settleUntil(
      () =>
        said() ===
        'Someone else changed this recurring expense. It was reloaded: check it and try again.',
    );
  });

  it.each(closeRoutes)(
    'closing by %s after a failed pause refetches and holds the actions',
    async (_route, close) => {
      let release: () => void = () => {};
      let held = false;
      const api = stub(TEMPLATE_RENT, {
        [templatePatch(TEMPLATE_RENT.id)]: lost,
        [templateGet(TEMPLATE_RENT.id)]: () =>
          held
            ? new Promise<Response>((resolve) => {
                release = () => resolve(jsonResponse(200, TEMPLATE_RENT));
              })
            : jsonResponse(200, TEMPLATE_RENT),
      });
      await openDetail();
      await click(action('Pause')!);
      await click(buttonNamed(confirmDialog()!, 'Pause'));
      await settleUntil(() => alerts(confirmDialog()!).length > 0);
      const before = api.calls(templateGet(TEMPLATE_RENT.id)).length;
      held = true;
      await close(confirmDialog()!);
      await settleUntil(() => confirmDialog() === null);
      await settleUntil(
        () => api.calls(templateGet(TEMPLATE_RENT.id)).length > before,
      );
      expect(action('Pause')!.disabled).toBe(true);
      expect(action('Edit')!.disabled).toBe(true);
      release();
      await settleUntil(() => !action('Pause')!.disabled);
      expect(main().textContent).not.toMatch(/reloaded/);
    },
  );
});

describe('End (D4)', () => {
  it('defaults to today and sets ends_on', async () => {
    const api = stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () =>
        jsonResponse(200, {
          ...TEMPLATE_RENT,
          ends_on: '2026-10-20',
          status: 'ended',
          version: 2,
        }),
    });
    await openDetail();
    await click(action('End')!);
    await settleUntil(() => dialog('end')?.querySelector('form'));
    const field = labelled<HTMLInputElement>(dialog('end')!, 'End date');
    expect(field.value).toBe('2026-10-20');
    expect(field.min).toBe('2026-10-01');
    await click(buttonNamed(dialog('end')!, 'End'));
    await settleUntil(() => !dialog('end'));
    expect(api.calls(templatePatch(TEMPLATE_RENT.id))[0]!.body).toEqual({
      version: 1,
      ends_on: '2026-10-20',
    });
    expect(said()).toBe('Recurring expense ended.');
  });

  it('a later end keeps it live and says until when', async () => {
    stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () =>
        jsonResponse(200, {
          ...TEMPLATE_RENT,
          ends_on: '2027-03-31',
          version: 2,
        }),
    });
    await openDetail();
    await click(action('End')!);
    await settleUntil(() => dialog('end')?.querySelector('form'));
    setFieldValue(labelled(dialog('end')!, 'End date'), '2027-03-31');
    await click(buttonNamed(dialog('end')!, 'End'));
    await settleUntil(() => !dialog('end'));
    expect(said()).toBe(
      'End date saved. The last period starts on Mar 31, 2027 at the latest.',
    );
  });

  it('refuses before the latest generated period, client-side and from the server', async () => {
    const api = stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () =>
        problemWith(422, 'expense_template_ends_on_invalid', {
          errors: [
            {
              path: ['body', 'ends_on'],
              code: 'expense_template_ends_on_invalid',
            },
          ],
        }),
    });
    await openDetail();
    await click(action('End')!);
    await settleUntil(() => dialog('end')?.querySelector('form'));
    setFieldValue(labelled(dialog('end')!, 'End date'), '2026-09-30');
    await click(buttonNamed(dialog('end')!, 'End'));
    expect(api.calls(templatePatch(TEMPLATE_RENT.id))).toHaveLength(0);
    const text =
      "The end can't be before the start or before the latest period already generated.";
    expect(fieldError(dialog('end')!, 'End date')).toBe(text);
    setFieldValue(labelled(dialog('end')!, 'End date'), '2026-10-05');
    await click(buttonNamed(dialog('end')!, 'End'));
    await settleUntil(
      () => api.calls(templatePatch(TEMPLATE_RENT.id)).length === 1,
    );
    await settleUntil(() => fieldError(dialog('end')!, 'End date') === text);
  });
});

describe('a failed save (E2 (a), D6)', () => {
  it.each(closeRoutes)(
    'closing the edit by %s after a failed save refetches and holds the actions',
    async (_route, close) => {
      let release: () => void = () => {};
      let held = false;
      const api = stub(TEMPLATE_RENT, {
        [templatePatch(TEMPLATE_RENT.id)]: lost,
        [templateGet(TEMPLATE_RENT.id)]: () =>
          held
            ? new Promise<Response>((resolve) => {
                release = () => resolve(jsonResponse(200, TEMPLATE_RENT));
              })
            : jsonResponse(200, TEMPLATE_RENT),
      });
      await openDetail();
      await openEdit();
      setFieldValue(labelled(dialog('edit')!, 'Amount of each expense'), '1');
      await click(buttonNamed(dialog('edit')!, 'Save'));
      await settleUntil(() => alerts(dialog('edit')!).length > 0);
      const before = api.calls(templateGet(TEMPLATE_RENT.id)).length;
      held = true;
      await close(dialog('edit')!);
      await settleUntil(() => !dialog('edit'));
      await settleUntil(
        () => api.calls(templateGet(TEMPLATE_RENT.id)).length > before,
      );
      expect(action('Edit')!.disabled).toBe(true);
      release();
      await settleUntil(() => !action('Edit')!.disabled);
    },
  );

  it('a failed refetch keeps the template with the out-of-date alert, never "reloaded"', async () => {
    let failing = false;
    stub(TEMPLATE_RENT, {
      [templatePatch(TEMPLATE_RENT.id)]: () => problem(500, 'internal_error'),
      [templateGet(TEMPLATE_RENT.id)]: () =>
        failing
          ? problem(503, 'database_unavailable')
          : jsonResponse(200, TEMPLATE_RENT),
    });
    await openDetail();
    await click(action('End')!);
    await settleUntil(() => dialog('end')?.querySelector('form'));
    await click(buttonNamed(dialog('end')!, 'End'));
    await settleUntil(() => alerts(dialog('end')!).length > 0);
    failing = true;
    await click(buttonNamed(dialog('end')!, 'Cancel'));
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain(
      "The recurring expense couldn't be refreshed and may be out of date.",
    );
    expect(main().textContent).not.toMatch(/reloaded/);
    expect(main().querySelector('h1')?.textContent).toBe('Renta del local');
    expect(action('Edit')!.disabled).toBe(true);
    failing = false;
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => !action('Edit')!.disabled);
  });

  it('a close with no attempt makes no request', async () => {
    const api = stub();
    await openDetail();
    await openEdit();
    const before = api.requests.length;
    await click(buttonNamed(dialog('edit')!, 'Cancel'));
    await settle();
    await click(action('Pause')!);
    await click(buttonNamed(confirmDialog()!, 'Cancel'));
    await settle();
    expect(api.requests.length).toBe(before);
    expect(action('Edit')!.disabled).toBe(false);
  });
});
