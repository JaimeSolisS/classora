import { parseLocalDate } from '@/lib/time';

// The audit page's filters live in the URL (#120 C1): ?record_type=…
// &record_id=…&actor_id=…&from=YYYY-MM-DD&to=YYYY-MM-DD, so back/forward
// and links (a row's "history" buttons, the user detail page) restore them.
// Values are kept as written: validation (C2) decides whether a request is
// sent, and an unknown record type is sent as is (it gives an empty list).

export interface AuditFilters {
  recordType: string | null;
  recordId: string | null;
  actorId: string | null;
  /** Center-local calendar dates (E5). */
  from: string | null;
  to: string | null;
}

export const NO_FILTERS: AuditFilters = {
  recordType: null,
  recordId: null,
  actorId: null,
  from: null,
  to: null,
};

const PARAMS = {
  recordType: 'record_type',
  recordId: 'record_id',
  actorId: 'actor_id',
  from: 'from',
  to: 'to',
} as const satisfies Record<keyof AuditFilters, string>;

const KEYS = Object.keys(PARAMS) as (keyof AuditFilters)[];

export function filtersFromParams(params: URLSearchParams): AuditFilters {
  const filters = { ...NO_FILTERS };
  for (const key of KEYS) {
    const value = params.get(PARAMS[key])?.trim();
    filters[key] = value ? value : null;
  }
  return filters;
}

/** The URL query of `filters` (unset ones left out). */
export function paramsFor(filters: AuditFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of KEYS) {
    const value = filters[key];
    if (value) {
      params.set(PARAMS[key], value);
    }
  }
  return params;
}

export function isFiltered(filters: AuditFilters): boolean {
  return KEYS.some((key) => filters[key] !== null);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** The API's record_type pattern (`^[a-z_]{1,63}$`). */
const RECORD_TYPE = /^[a-z_]{1,63}$/;

export function isUuid(value: string): boolean {
  return UUID.test(value);
}

function isLocalDate(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** What is wrong with a set of filters (C2); empty when it can be sent. */
export interface FilterErrors {
  /** A type without an ID, or an ID without a type. */
  record?: 'pair';
  /** The record type is not a table-name code (a hand-edited URL). */
  recordType?: 'invalid';
  /** The record ID is not a UUID. */
  recordId?: 'invalid';
  /** The person filter is not a valid id (a hand-edited URL). */
  actorId?: 'invalid';
  from?: 'invalid';
  to?: 'invalid' | 'beforeFrom';
}

export function validateFilters(filters: AuditFilters): FilterErrors {
  const errors: FilterErrors = {};
  const { recordType, recordId, actorId, from, to } = filters;
  if ((recordType === null) !== (recordId === null)) {
    errors.record = 'pair';
  } else if (recordType !== null && recordId !== null) {
    if (!RECORD_TYPE.test(recordType)) {
      errors.recordType = 'invalid';
    }
    if (!isUuid(recordId)) {
      errors.recordId = 'invalid';
    }
  }
  if (actorId !== null && !isUuid(actorId)) {
    errors.actorId = 'invalid';
  }
  const fromValid = from === null || isLocalDate(from);
  const toValid = to === null || isLocalDate(to);
  if (!fromValid) {
    errors.from = 'invalid';
  }
  if (!toValid) {
    errors.to = 'invalid';
  } else if (from !== null && to !== null && fromValid && to < from) {
    errors.to = 'beforeFrom';
  }
  return errors;
}

export function hasErrors(errors: FilterErrors): boolean {
  return Object.keys(errors).length > 0;
}
