import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The price catalog calls (#89, #363): the list and one item are queries;
// creating an item, scheduling a version and deleting an upcoming version
// are direct calls after which the pages invalidate the price queries. The
// dialog's choices (subjects, levels, cohorts, groups) are read here too.

export type ServicePrice = components['schemas']['ServicePriceItem'];
export type ServicePriceDetail = components['schemas']['ServicePriceDetail'];
export type ServicePricePage =
  components['schemas']['OffsetPage_ServicePriceItem_'];
export type ServicePriceCreate = components['schemas']['ServicePriceCreate'];
export type PriceVersion = components['schemas']['PriceVersionItem'];
export type PriceVersionCreate = components['schemas']['PriceVersionCreate'];
export type ClassType = components['schemas']['ClassType'];
export type PricingModel = components['schemas']['CatalogPricingModel'];

/** Rows per page of the price list (the API default). */
export const PRICES_PAGE_SIZE = 25;
/** The largest page the option lists ask for. */
const OPTIONS_PAGE_SIZE = 100;
/** At most this many pages of options are read (10 000 items). */
const OPTIONS_MAX_PAGES = 100;

/** Every cached price query (the list pages and each item). */
export const PRICES_QUERY_KEY = ['billing', 'service-prices'] as const;
/** The dialog's choices. */
export const PRICE_OPTIONS_KEY = ['billing', 'price-options'] as const;

export interface PriceListFilters {
  classType: ClassType | null;
  pricingModel: PricingModel | null;
  page: number;
}

export function pricesListKey(filters: PriceListFilters) {
  return [...PRICES_QUERY_KEY, 'list', filters] as const;
}

export function priceKey(priceId: string) {
  return [...PRICES_QUERY_KEY, 'detail', priceId] as const;
}

export function fetchPrices(
  filters: PriceListFilters,
  signal: AbortSignal,
): Promise<ServicePricePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/service-prices', {
        params: {
          query: {
            ...(filters.classType ? { class_type: filters.classType } : {}),
            ...(filters.pricingModel
              ? { pricing_model: filters.pricingModel }
              : {}),
            limit: PRICES_PAGE_SIZE,
            offset: (filters.page - 1) * PRICES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchPrice(
  priceId: string,
  signal: AbortSignal,
): Promise<ServicePriceDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/service-prices/{service_price_id}', {
        params: { path: { service_price_id: priceId } },
        signal,
      }),
    { signal },
  );
}

export function createPrice(
  body: ServicePriceCreate,
): Promise<ServicePriceDetail> {
  return apiCall(() => api.POST('/api/v1/billing/service-prices', { body }));
}

export function createPriceVersion(
  priceId: string,
  body: PriceVersionCreate,
): Promise<ServicePriceDetail> {
  return apiCall(() =>
    api.POST('/api/v1/billing/service-prices/{service_price_id}/versions', {
      params: { path: { service_price_id: priceId } },
      body,
    }),
  );
}

export async function deletePriceVersion(
  priceId: string,
  versionId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE(
      '/api/v1/billing/service-prices/{service_price_id}/versions/{price_version_id}',
      {
        params: {
          path: { service_price_id: priceId, price_version_id: versionId },
        },
      },
    ),
  );
}

// --------------------------------------------------------- the choices

export interface Option {
  id: string;
  name: string;
}

export interface SubjectOption extends Option {
  levelName: string;
}

/** Every page of an offset list, `OPTIONS_PAGE_SIZE` items at a time. */
async function everyOffsetPage<T>(
  read: (offset: number) => Promise<{ items: T[]; total: number }>,
): Promise<T[]> {
  const items: T[] = [];
  for (let page = 0; page < OPTIONS_MAX_PAGES; page += 1) {
    const result = await read(page * OPTIONS_PAGE_SIZE);
    items.push(...result.items);
    if (result.items.length === 0 || items.length >= result.total) {
      break;
    }
  }
  return items;
}

/** Every page of a cursor list. */
async function everyCursorPage<T>(
  read: (
    cursor: string | null,
  ) => Promise<{ items: T[]; next_cursor?: string | null }>,
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < OPTIONS_MAX_PAGES; page += 1) {
    const result = await read(cursor);
    items.push(...result.items);
    cursor = result.next_cursor ?? null;
    if (!cursor) {
      break;
    }
  }
  return items;
}

/** Every active subject, in the API's order (level order, then name). */
export async function fetchSubjectOptions(
  signal: AbortSignal,
): Promise<SubjectOption[]> {
  const subjects = await everyOffsetPage((offset) =>
    apiCall(
      () =>
        api.GET('/api/v1/subjects', {
          params: {
            query: { status: 'active', limit: OPTIONS_PAGE_SIZE, offset },
          },
          signal,
        }),
      { signal },
    ),
  );
  return subjects.map((subject) => ({
    id: subject.id,
    name: subject.name,
    levelName: subject.education_level_name,
  }));
}

/** Every active education level, in their order. */
export async function fetchLevelOptions(
  signal: AbortSignal,
): Promise<Option[]> {
  const levels = await everyOffsetPage((offset) =>
    apiCall(
      () =>
        api.GET('/api/v1/education-levels', {
          params: {
            query: { status: 'active', limit: OPTIONS_PAGE_SIZE, offset },
          },
          signal,
        }),
      { signal },
    ),
  );
  return levels.map((level) => ({ id: level.id, name: level.name }));
}

export type ScopeKind = 'cohort' | 'group';

/**
 * The cohorts (upcoming and in progress) or the groups (active) of a
 * subject, by name: a price can be scoped only to one of the subject's.
 */
export async function fetchScopeOptions(
  kind: ScopeKind,
  subjectId: string,
  signal: AbortSignal,
): Promise<Option[]> {
  const items =
    kind === 'cohort'
      ? await everyCursorPage((cursor) =>
          apiCall(
            () =>
              api.GET('/api/v1/cohorts', {
                params: {
                  query: {
                    subject_id: subjectId,
                    status: 'current',
                    limit: OPTIONS_PAGE_SIZE,
                    ...(cursor ? { cursor } : {}),
                  },
                },
                signal,
              }),
            { signal },
          ),
        )
      : await everyCursorPage((cursor) =>
          apiCall(
            () =>
              api.GET('/api/v1/groups', {
                params: {
                  query: {
                    subject_id: subjectId,
                    status: 'active',
                    limit: OPTIONS_PAGE_SIZE,
                    ...(cursor ? { cursor } : {}),
                  },
                },
                signal,
              }),
            { signal },
          ),
        );
  return items.map((item) => ({ id: item.id, name: item.name }));
}

export function scopeOptionsKey(kind: ScopeKind, subjectId: string) {
  return [...PRICE_OPTIONS_KEY, kind, subjectId] as const;
}
