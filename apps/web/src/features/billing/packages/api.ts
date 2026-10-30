import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { PICKER_LIMIT } from '@/features/guardians/api';
import { idempotencyHeaders } from '@/lib/idempotency';
import type { PriceVersionCreate } from '../pricing/api';

// The package calls (#94, #377): the product list, a product, the sold
// list and a package are queries; creating and changing a product, its
// price versions and a sale are direct calls after which the pages
// invalidate the package queries. A sale posts a ledger row, so it sends
// an `Idempotency-Key` (#88) that the caller computes outside the call.

export type PackageProduct = components['schemas']['PackageProductItem'];
export type PackageProductDetail =
  components['schemas']['PackageProductDetail'];
export type PackageProductPage =
  components['schemas']['OffsetPage_PackageProductItem_'];
export type PackageProductCreate =
  components['schemas']['PackageProductCreate'];
export type PackageProductUpdate =
  components['schemas']['PackageProductUpdate'];
export type SoldPackage = components['schemas']['PackageItem'];
export type PackageDetail = components['schemas']['PackageDetail'];
export type PackagePage = components['schemas']['OffsetPage_PackageItem_'];
export type PackagePurchase = components['schemas']['PackagePurchase'];
export type PackageStatus = components['schemas']['PackageStatus'];
export type PackageTransaction =
  components['schemas']['PackageTransactionItem'];
export type ChargeItem = components['schemas']['ChargeItem'];
export type PackageCancellationCreate =
  components['schemas']['PackageCancellationCreate'];
export type PackageCancelled = components['schemas']['PackageCancelled'];

export const PACKAGE_STATUSES: readonly PackageStatus[] = [
  'active',
  'exhausted',
  'expired',
  'cancelled',
];

/** Rows per page of both lists (the API default). */
export const PACKAGES_PAGE_SIZE = 25;
const OPTIONS_PAGE_SIZE = 100;
const OPTIONS_MAX_PAGES = 50;
const CHARGES_PAGE_SIZE = 100;
const CHARGES_MAX_PAGES = 20;

/** Every cached product query (the list pages, each product, the choices). */
export const PRODUCTS_QUERY_KEY = ['billing', 'package-products'] as const;
/** Every cached sold-package query (the list pages and each package). */
export const PACKAGES_QUERY_KEY = ['billing', 'packages'] as const;
/** The pickers' searches and the names of deep-linked filters. */
export const PACKAGE_OPTIONS_KEY = ['billing', 'package-options'] as const;

export interface ProductListFilters {
  /** null: active and inactive. */
  isActive: boolean | null;
  subjectId: string | null;
  page: number;
}

export interface PackageListFilters {
  /** Empty: every status. */
  statuses: PackageStatus[];
  studentId: string | null;
  familyId: string | null;
  page: number;
}

const offsetOf = (page: number) => (page - 1) * PACKAGES_PAGE_SIZE;

// ------------------------------------------------------------- products

export function productsListKey(filters: ProductListFilters) {
  return [...PRODUCTS_QUERY_KEY, 'list', filters] as const;
}

export function productKey(productId: string) {
  return [...PRODUCTS_QUERY_KEY, 'detail', productId] as const;
}

export const ACTIVE_PRODUCTS_KEY = [...PRODUCTS_QUERY_KEY, 'active'] as const;

export function fetchProducts(
  filters: ProductListFilters,
  signal: AbortSignal,
): Promise<PackageProductPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/package-products', {
        params: {
          query: {
            ...(filters.isActive !== null
              ? { is_active: filters.isActive }
              : {}),
            ...(filters.subjectId ? { subject_id: filters.subjectId } : {}),
            limit: PACKAGES_PAGE_SIZE,
            offset: offsetOf(filters.page),
          },
        },
        signal,
      }),
    { signal },
  );
}

/** Every active product (the sell dialog's choices), in the API's order. */
export async function fetchActiveProducts(
  signal: AbortSignal,
): Promise<PackageProduct[]> {
  const items: PackageProduct[] = [];
  for (let page = 0; page < OPTIONS_MAX_PAGES; page += 1) {
    const result = await apiCall(
      () =>
        api.GET('/api/v1/billing/package-products', {
          params: {
            query: {
              is_active: true,
              limit: OPTIONS_PAGE_SIZE,
              offset: page * OPTIONS_PAGE_SIZE,
            },
          },
          signal,
        }),
      { signal },
    );
    items.push(...result.items);
    if (result.items.length === 0 || items.length >= result.total) {
      break;
    }
  }
  return items.filter((item) => item.is_active);
}

export function fetchProduct(
  productId: string,
  signal: AbortSignal,
): Promise<PackageProductDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/package-products/{product_id}', {
        params: { path: { product_id: productId } },
        signal,
      }),
    { signal },
  );
}

export function createProduct(
  body: PackageProductCreate,
): Promise<PackageProductDetail> {
  return apiCall(() => api.POST('/api/v1/billing/package-products', { body }));
}

export function updateProduct(
  productId: string,
  body: PackageProductUpdate,
): Promise<PackageProductDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/billing/package-products/{product_id}', {
      params: { path: { product_id: productId } },
      body,
    }),
  );
}

export function createProductVersion(
  productId: string,
  body: PriceVersionCreate,
): Promise<PackageProductDetail> {
  return apiCall(() =>
    api.POST('/api/v1/billing/package-products/{product_id}/versions', {
      params: { path: { product_id: productId } },
      body,
    }),
  );
}

export async function deleteProductVersion(
  productId: string,
  versionId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE(
      '/api/v1/billing/package-products/{product_id}/versions/{price_version_id}',
      {
        params: {
          path: { product_id: productId, price_version_id: versionId },
        },
      },
    ),
  );
}

// ------------------------------------------------------- sold packages

export function packagesListKey(filters: PackageListFilters) {
  return [...PACKAGES_QUERY_KEY, 'list', filters] as const;
}

export function packageKey(packageId: string) {
  return [...PACKAGES_QUERY_KEY, 'detail', packageId] as const;
}

export function fetchPackages(
  filters: PackageListFilters,
  signal: AbortSignal,
): Promise<PackagePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/packages', {
        params: {
          query: {
            ...(filters.statuses.length > 0
              ? { status: filters.statuses }
              : {}),
            ...(filters.studentId ? { student_id: filters.studentId } : {}),
            ...(filters.familyId ? { family_id: filters.familyId } : {}),
            limit: PACKAGES_PAGE_SIZE,
            offset: offsetOf(filters.page),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchPackage(
  packageId: string,
  signal: AbortSignal,
): Promise<PackageDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/packages/{package_id}', {
        params: { path: { package_id: packageId } },
        signal,
      }),
    { signal },
  );
}

/**
 * Sells a package (#94 C1). `idempotencyKey` comes from the caller's
 * `IdempotentSubmission.keyFor(body)`, computed before this call so that
 * apiCall's own retries resend the same key.
 */
export function purchasePackage(
  body: PackagePurchase,
  idempotencyKey: string,
): Promise<PackageDetail> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/billing/packages', {
      body,
      params: { header: headers },
    }),
  );
}

/**
 * The purchase charge of a package (#377 E1), read from its family's
 * charges for the student. They come newest service date first, so the
 * search stops at the first page that reaches an earlier date than the
 * purchase. Null when it isn't found.
 */
export async function fetchPurchaseCharge(
  pkg: Pick<
    PackageDetail,
    'family' | 'student' | 'purchase_charge_id' | 'purchased_on'
  >,
  signal: AbortSignal,
): Promise<ChargeItem | null> {
  const chargeId = pkg.purchase_charge_id;
  if (!chargeId) {
    return null;
  }
  for (let page = 0; page < CHARGES_MAX_PAGES; page += 1) {
    const result = await apiCall(
      () =>
        api.GET('/api/v1/families/{family_id}/billing-account/charges', {
          params: {
            path: { family_id: pkg.family.id },
            query: {
              student_id: pkg.student.id,
              limit: CHARGES_PAGE_SIZE,
              offset: page * CHARGES_PAGE_SIZE,
            },
          },
          signal,
        }),
      { signal },
    );
    const found = result.items.find((charge) => charge.id === chargeId);
    if (found) {
      return found;
    }
    const last = result.items.at(-1);
    if (
      !last ||
      last.service_date < pkg.purchased_on ||
      (page + 1) * CHARGES_PAGE_SIZE >= result.total
    ) {
      return null;
    }
  }
  return null;
}

/**
 * Cancels a package purchase (#100 C5; #386 C1): the units left are taken
 * back and the purchase charge is credited. The key as in
 * `purchasePackage`; the route needs a recent password confirmation (the
 * apiCall dialog, then the same key again).
 */
export function cancelPackage(
  packageId: string,
  body: PackageCancellationCreate,
  idempotencyKey: string,
): Promise<PackageCancelled> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/billing/packages/{package_id}/cancellation', {
      body,
      params: { path: { package_id: packageId }, header: headers },
    }),
  );
}

export function purchaseChargeKey(packageId: string) {
  return [...PACKAGES_QUERY_KEY, 'charge', packageId] as const;
}

// --------------------------------------------------------- the choices

export interface NamedOption {
  id: string;
  name: string;
}

/** Up to PICKER_LIMIT families matching `q`, any status (guardian.view). */
export async function searchFamilies(
  q: string,
  signal: AbortSignal,
): Promise<NamedOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/families', {
        params: { query: { q, status: 'all', limit: PICKER_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items.map((family) => ({ id: family.id, name: family.name }));
}

/** A family by id, with its name (the list's deep link ?family_id=). */
export async function fetchFamilyOption(
  familyId: string,
  signal: AbortSignal,
): Promise<NamedOption> {
  const family = await apiCall(
    () =>
      api.GET('/api/v1/families/{family_id}', {
        params: { path: { family_id: familyId } },
        signal,
      }),
    { signal },
  );
  return { id: family.id, name: family.name };
}

export function familyOptionKey(familyId: string) {
  return [...PACKAGE_OPTIONS_KEY, 'family', familyId] as const;
}

export function studentOptionKey(studentId: string) {
  return [...PACKAGE_OPTIONS_KEY, 'student', studentId] as const;
}
