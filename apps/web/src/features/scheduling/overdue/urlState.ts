import { parseLocalDate, type LocalDate } from '@/lib/time';
import { OVERDUE_PAGE_SIZE, type OverdueQuery } from './api';

// The overdue page's state in the URL (#301b A5, the calendar's urlState
// style): `tutor` (a UUID), `from` and `to` (center-local dates) and `page`
// (1-based). Anything invalid is ignored. Pure.

export interface OverdueFilters {
  tutor: string | null;
  from: LocalDate | null;
  to: LocalDate | null;
}

export interface OverdueState extends OverdueFilters {
  page: number;
}

export const NO_OVERDUE_FILTERS: OverdueFilters = {
  tutor: null,
  from: null,
  to: null,
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function idParam(value: string | null): string | null {
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function dateParam(value: string | null): LocalDate | null {
  if (value === null) {
    return null;
  }
  try {
    return parseLocalDate(value);
  } catch {
    return null;
  }
}

function pageParam(value: string | null): number {
  return value !== null && /^[1-9]\d{0,5}$/.test(value) ? Number(value) : 1;
}

export function parseOverdueState(params: URLSearchParams): OverdueState {
  return {
    tutor: idParam(params.get('tutor')),
    from: dateParam(params.get('from')),
    to: dateParam(params.get('to')),
    page: pageParam(params.get('page')),
  };
}

/** The search params of a state (only what is set; page from 2). */
export function overdueSearchParams(state: OverdueState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.tutor) params.set('tutor', state.tutor);
  if (state.from) params.set('from', state.from);
  if (state.to) params.set('to', state.to);
  if (state.page > 1) params.set('page', String(state.page));
  return params;
}

export function hasOverdueFilters(filters: OverdueFilters): boolean {
  return filters.tutor !== null || filters.from !== null || filters.to !== null;
}

/** `to` before `from`: the range error, and no request is sent. */
export function rangeInvalid(filters: OverdueFilters): boolean {
  return (
    filters.from !== null && filters.to !== null && filters.to < filters.from
  );
}

/** The list read's query for a state. */
export function overdueQuery(state: OverdueState): OverdueQuery {
  const query: OverdueQuery = {
    limit: OVERDUE_PAGE_SIZE,
    offset: (state.page - 1) * OVERDUE_PAGE_SIZE,
  };
  if (state.tutor) query.tutor_user_id = state.tutor;
  if (state.from) query.from = state.from;
  if (state.to) query.to = state.to;
  return query;
}
