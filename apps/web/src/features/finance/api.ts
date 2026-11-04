import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The expense category and vendor calls (#114 B, C; M7-11b). No
// Idempotency-Key: a name is a natural key, so a repeated create can only end
// in `409 …_exists` (#114 E4).

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
