import type { VendorListParams, VendorStateFilter } from '../api';

// The vendor list's URL (M7-11b C1): `q`, `state` (`active` or `inactive`;
// absent is every vendor) and `page`. Anything invalid is ignored.

/** The API's `q` limit (#114). */
export const SEARCH_MAX_LENGTH = 100;

export const VENDOR_STATE_FILTERS: readonly VendorStateFilter[] = [
  'active',
  'inactive',
  'all',
];

export const DEFAULT_VENDOR_STATE: VendorStateFilter = 'all';

export function vendorListParams(search: URLSearchParams): VendorListParams {
  const state = search.get('state') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    state:
      VENDOR_STATE_FILTERS.find((value) => value === state) ??
      DEFAULT_VENDOR_STATE,
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** The URL's search string for `params` (defaults left out). */
export function vendorListSearch(params: VendorListParams): string {
  const next = new URLSearchParams();
  if (params.q) {
    next.set('q', params.q);
  }
  if (params.state !== DEFAULT_VENDOR_STATE) {
    next.set('state', params.state);
  }
  if (params.page > 1) {
    next.set('page', String(params.page));
  }
  return next.toString();
}
