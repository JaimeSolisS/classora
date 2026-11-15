import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { ServicePrice } from '../pricing/api';
import type { TargetKind } from '../targets';

// The custom price calls (#95, #367 E): the list is a query; creating,
// ending and deleting are direct calls after which the page invalidates
// the custom price queries. The add dialog's catalog prices are read here
// too (every page of the price catalog).

export type CustomPrice = components['schemas']['CustomPriceItem'];
export type CustomPricePage =
  components['schemas']['OffsetPage_CustomPriceItem_'];
export type CustomPriceCreate = components['schemas']['CustomPriceCreate'];
export type CustomPriceStatus = components['schemas']['CustomPriceStatus'];

/** Rows per page of the list (the API default). */
export const CUSTOM_PRICES_PAGE_SIZE = 25;
const CATALOG_PAGE_SIZE = 100;
const CATALOG_MAX_PAGES = 100;

/** Every cached custom price query. */
export const CUSTOM_PRICES_QUERY_KEY = ['billing', 'custom-prices'] as const;
/** The add dialog's catalog prices. */
export const CATALOG_OPTIONS_KEY = ['billing', 'custom-price-catalog'] as const;

/**
 * The status filter: `open` (the default) is current and upcoming, `all`
 * every status, otherwise one status.
 */
export type CustomPriceStatusChoice = 'open' | 'all' | CustomPriceStatus;

export interface CustomPriceListFilters {
  target: { kind: TargetKind; id: string } | null;
  status: CustomPriceStatusChoice;
  page: number;
}

export function statusesOf(
  choice: CustomPriceStatusChoice,
): CustomPriceStatus[] {
  if (choice === 'open') return ['current', 'upcoming'];
  if (choice === 'all') return [];
  return [choice];
}

export function customPricesListKey(filters: CustomPriceListFilters) {
  return [...CUSTOM_PRICES_QUERY_KEY, 'list', filters] as const;
}

export function fetchCustomPrices(
  filters: CustomPriceListFilters,
  signal: AbortSignal,
): Promise<CustomPricePage> {
  const statuses = statusesOf(filters.status);
  return apiCall(
    () =>
      api.GET('/api/v1/billing/custom-prices', {
        params: {
          query: {
            ...(filters.target?.kind === 'student'
              ? { student_id: filters.target.id }
              : {}),
            ...(filters.target?.kind === 'family'
              ? { family_id: filters.target.id }
              : {}),
            ...(statuses.length > 0 ? { status: statuses } : {}),
            limit: CUSTOM_PRICES_PAGE_SIZE,
            offset: (filters.page - 1) * CUSTOM_PRICES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function createCustomPrice(
  body: CustomPriceCreate,
): Promise<CustomPrice> {
  return apiCall(() => api.POST('/api/v1/billing/custom-prices', { body }));
}

export function endCustomPrice(
  customPriceId: string,
  body: { version: number; expires_on: string },
): Promise<CustomPrice> {
  return apiCall(() =>
    api.PATCH('/api/v1/billing/custom-prices/{custom_price_id}', {
      params: { path: { custom_price_id: customPriceId } },
      body,
    }),
  );
}

export async function deleteCustomPrice(customPriceId: string): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/billing/custom-prices/{custom_price_id}', {
      params: { path: { custom_price_id: customPriceId } },
    }),
  );
}

/**
 * Every catalog price a custom price can be for (per session or full
 * course, #95 E3), in the catalog's order.
 */
export async function fetchCatalogOptions(
  signal: AbortSignal,
): Promise<ServicePrice[]> {
  const items: ServicePrice[] = [];
  for (let page = 0; page < CATALOG_MAX_PAGES; page += 1) {
    const result = await apiCall(
      () =>
        api.GET('/api/v1/billing/service-prices', {
          params: {
            query: {
              limit: CATALOG_PAGE_SIZE,
              offset: page * CATALOG_PAGE_SIZE,
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
  return items.filter(
    (item) =>
      item.pricing_model === 'per_session' ||
      item.pricing_model === 'full_course',
  );
}
