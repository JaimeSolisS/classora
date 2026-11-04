import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { idempotencyHeaders } from '@/lib/idempotency';

// The finance calls. Expense categories and vendors (#114 B, C; M7-11b) send
// no Idempotency-Key: a name is a natural key, so a repeated create can only
// end in `409 …_exists` (#114 E4). Expenses (#115, M7-12b) are below.

export type ExpenseCategory = components['schemas']['ExpenseCategoryItem'];
export type ExpenseCategoryCreate =
  components['schemas']['ExpenseCategoryCreate'];
export type ExpenseCategoryUpdate =
  components['schemas']['ExpenseCategoryUpdate'];
export type SystemKey = components['schemas']['SystemKey'];
export type Vendor = components['schemas']['VendorItem'];
export type VendorCreate = components['schemas']['VendorCreate'];
export type VendorUpdate = components['schemas']['VendorUpdate'];
export type VendorPage = components['schemas']['OffsetPage_VendorItem_'];

/** Every cached finance query. */
export const FINANCE_KEY = ['finance'] as const;
export const EXPENSE_CATEGORIES_KEY = [...FINANCE_KEY, 'categories'] as const;
/** Every cached page of vendors, whatever the filters. */
export const VENDORS_KEY = [...FINANCE_KEY, 'vendors'] as const;

export const VENDORS_PAGE_SIZE = 25;

/** The vendor list's state filter: absent from the URL is every vendor. */
export type VendorStateFilter = 'all' | 'active' | 'inactive';

export interface VendorListParams {
  q: string;
  state: VendorStateFilter;
  /** 1-based. */
  page: number;
}

export function vendorsListKey(params: VendorListParams) {
  return [...VENDORS_KEY, 'list', params] as const;
}

export function fetchExpenseCategories(
  signal: AbortSignal,
): Promise<ExpenseCategory[]> {
  return apiCall(
    () => api.GET('/api/v1/finance/expense-categories', { signal }),
    { signal },
  );
}

export function createExpenseCategory(
  body: ExpenseCategoryCreate,
): Promise<ExpenseCategory> {
  return apiCall(() =>
    api.POST('/api/v1/finance/expense-categories', { body }),
  );
}

export function updateExpenseCategory(
  categoryId: string,
  body: ExpenseCategoryUpdate,
): Promise<ExpenseCategory> {
  return apiCall(() =>
    api.PATCH('/api/v1/finance/expense-categories/{category_id}', {
      params: { path: { category_id: categoryId } },
      body,
    }),
  );
}

export function fetchVendors(
  { q, state, page }: VendorListParams,
  signal: AbortSignal,
): Promise<VendorPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/finance/vendors', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(state === 'all' ? {} : { active: state === 'active' }),
            limit: VENDORS_PAGE_SIZE,
            offset: (page - 1) * VENDORS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function createVendor(body: VendorCreate): Promise<Vendor> {
  return apiCall(() => api.POST('/api/v1/finance/vendors', { body }));
}

export function updateVendor(
  vendorId: string,
  body: VendorUpdate,
): Promise<Vendor> {
  return apiCall(() =>
    api.PATCH('/api/v1/finance/vendors/{vendor_id}', {
      params: { path: { vendor_id: vendorId } },
      body,
    }),
  );
}

// ------------------------------------------------- expenses (#115, M7-12b)

export type ExpenseItem = components['schemas']['ExpenseItem'];
export type ExpenseDetail = components['schemas']['ExpenseDetail'];
export type ExpenseCreate = components['schemas']['ExpenseCreate'];
export type ExpenseCreated = components['schemas']['ExpenseCreated'];
export type ExpenseStatus = components['schemas']['ExpenseStatus'];
export type ExpensePage = components['schemas']['OffsetPage_ExpenseItem_'];
export type ExpensePayment = components['schemas']['ExpensePaymentItem'];
export type ExpenseAdjustment = components['schemas']['ExpenseAdjustmentItem'];
export type ExpensePaymentCreate =
  components['schemas']['ExpensePaymentCreate'];
export type ExpensePaymentReversalCreate =
  components['schemas']['ExpensePaymentReversalCreate'];
export type ExpenseAdjustmentCreate =
  components['schemas']['ExpenseAdjustmentCreate'];

/** Every cached expense query: the lists and the details. */
export const EXPENSES_KEY = [...FINANCE_KEY, 'expenses'] as const;

export const EXPENSES_PAGE_SIZE = 25;
/** The API's largest page (core/pagination.py MAX_LIMIT). */
const MAX_PAGE = 100;
/** A ceiling on the vendor pages read for a picker or a filter. */
const MAX_VENDOR_PAGES = 50;

export type ExpenseSort = '-incurred_on' | 'due_on';

export interface ExpenseListParams {
  statuses: ExpenseStatus[];
  categoryId: string;
  vendorId: string;
  /** Incurred from / to (inclusive), '' for none. */
  from: string;
  to: string;
  q: string;
  sort: ExpenseSort;
  /** 1-based. */
  page: number;
}

export function expensesListKey(params: ExpenseListParams) {
  return [...EXPENSES_KEY, 'list', params] as const;
}

export function expenseKey(expenseId: string) {
  return [...EXPENSES_KEY, 'detail', expenseId] as const;
}

/** Every vendor, active and inactive, read page by page (at most 100 each). */
export const ALL_VENDORS_KEY = [...VENDORS_KEY, 'all'] as const;

export async function fetchAllVendors(signal: AbortSignal): Promise<Vendor[]> {
  const all: Vendor[] = [];
  for (let page = 0; page < MAX_VENDOR_PAGES; page += 1) {
    const offset = page * MAX_PAGE;
    const result = await apiCall(
      () =>
        api.GET('/api/v1/finance/vendors', {
          params: { query: { limit: MAX_PAGE, offset } },
          signal,
        }),
      { signal },
    );
    all.push(...result.items);
    if (result.items.length === 0 || all.length >= result.total) break;
  }
  return all;
}

export function fetchExpenses(
  params: ExpenseListParams,
  signal: AbortSignal,
): Promise<ExpensePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/finance/expenses', {
        params: {
          query: {
            ...(params.statuses.length > 0 ? { status: params.statuses } : {}),
            ...(params.categoryId ? { category_id: params.categoryId } : {}),
            ...(params.vendorId ? { vendor_id: params.vendorId } : {}),
            ...(params.from ? { incurred_from: params.from } : {}),
            ...(params.to ? { incurred_to: params.to } : {}),
            ...(params.q ? { q: params.q } : {}),
            sort: params.sort,
            limit: EXPENSES_PAGE_SIZE,
            offset: (params.page - 1) * EXPENSES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchExpense(
  expenseId: string,
  signal: AbortSignal,
): Promise<ExpenseDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/finance/expenses/{expense_id}', {
        params: { path: { expense_id: expenseId } },
        signal,
      }),
    { signal },
  );
}

// The four POSTs post money (#88): the key is computed by the caller, outside
// the closure, so apiCall's retries (csrf, the password) resend it. Their
// answers hold ids and the status only (#115 E7).

export function createExpense(
  body: ExpenseCreate,
  idempotencyKey: string,
): Promise<ExpenseCreated> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/finance/expenses', {
      params: { header: headers },
      body,
    }),
  );
}

export function recordExpensePayment(
  expenseId: string,
  body: ExpensePaymentCreate,
  idempotencyKey: string,
): Promise<unknown> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/finance/expenses/{expense_id}/payments', {
      params: { path: { expense_id: expenseId }, header: headers },
      body,
    }),
  );
}

export function reverseExpensePayment(
  paymentId: string,
  body: ExpensePaymentReversalCreate,
  idempotencyKey: string,
): Promise<unknown> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/finance/expense-payments/{payment_id}/reversal', {
      params: { path: { payment_id: paymentId }, header: headers },
      body,
    }),
  );
}

export function postExpenseAdjustment(
  expenseId: string,
  body: ExpenseAdjustmentCreate,
  idempotencyKey: string,
): Promise<unknown> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/finance/expenses/{expense_id}/adjustments', {
      params: { path: { expense_id: expenseId }, header: headers },
      body,
    }),
  );
}

// --------------------------------- recurring expense templates (#116, M7-13b)
// No Idempotency-Key: the name is unique, so a retried create can only end in
// `409 expense_template_exists` (#116 E3); an edit carries `version`.

export type ExpenseTemplate = components['schemas']['ExpenseTemplateItem'];
export type ExpenseTemplateCreate =
  components['schemas']['ExpenseTemplateCreate'];
export type ExpenseTemplateUpdate =
  components['schemas']['ExpenseTemplateUpdate'];
export type TemplateStatus = components['schemas']['TemplateStatus'];
export type Frequency = components['schemas']['Frequency'];

/** Every cached template query: the list and the details. */
export const TEMPLATES_KEY = [...FINANCE_KEY, 'templates'] as const;

export interface TemplateListParams {
  statuses: TemplateStatus[];
  categoryId: string;
  vendorId: string;
}

export function templatesListKey(params: TemplateListParams) {
  return [...TEMPLATES_KEY, 'list', params] as const;
}

export function templateKey(templateId: string) {
  return [...TEMPLATES_KEY, 'detail', templateId] as const;
}

/** A template's generated expenses: under EXPENSES_KEY, so any expense
 * change refreshes them too. */
export function templateExpensesKey(templateId: string, page: number) {
  return [...EXPENSES_KEY, 'template', templateId, page] as const;
}

export function fetchExpenseTemplates(
  params: TemplateListParams,
  signal: AbortSignal,
): Promise<ExpenseTemplate[]> {
  return apiCall(
    () =>
      api.GET('/api/v1/finance/expense-templates', {
        params: {
          query: {
            ...(params.statuses.length > 0 ? { status: params.statuses } : {}),
            ...(params.categoryId ? { category_id: params.categoryId } : {}),
            ...(params.vendorId ? { vendor_id: params.vendorId } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchExpenseTemplate(
  templateId: string,
  signal: AbortSignal,
): Promise<ExpenseTemplate> {
  return apiCall(
    () =>
      api.GET('/api/v1/finance/expense-templates/{template_id}', {
        params: { path: { template_id: templateId } },
        signal,
      }),
    { signal },
  );
}

/** The expenses a template generated, newest period first. */
export function fetchTemplateExpenses(
  templateId: string,
  page: number,
  signal: AbortSignal,
): Promise<ExpensePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/finance/expenses', {
        params: {
          query: {
            template_id: templateId,
            sort: '-incurred_on',
            limit: EXPENSES_PAGE_SIZE,
            offset: (page - 1) * EXPENSES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function createExpenseTemplate(
  body: ExpenseTemplateCreate,
): Promise<ExpenseTemplate> {
  return apiCall(() => api.POST('/api/v1/finance/expense-templates', { body }));
}

export function updateExpenseTemplate(
  templateId: string,
  body: ExpenseTemplateUpdate,
): Promise<ExpenseTemplate> {
  return apiCall(() =>
    api.PATCH('/api/v1/finance/expense-templates/{template_id}', {
      params: { path: { template_id: templateId } },
      body,
    }),
  );
}
