import {
  APPROVAL_TYPES,
  APPROVALS_PAGE_SIZE,
  ASSIGNEE_FILTERS,
  DEFAULT_FILTERS,
  PRIORITY_FILTERS,
  SORTS,
  STATUS_FILTERS,
  type ApprovalAssigneeFilter,
  type ApprovalFilters,
  type ApprovalPriority,
  type ApprovalSort,
  type ApprovalStatusFilter,
} from './api';

// The Approvals page's filters and page live in the URL (#40 F3, M8-01b
// A1): ?status=…&type=…&assignee=…&priority=…&sort=…&page=N. A missing or
// unknown value means the default, so an old or hand-edited link still
// opens the page. The open detail panel is `?item=<id>` (M8-01b B1), kept
// by the page beside these.

/** The search param of the open detail panel (M8-01b E1). */
export const ITEM_PARAM = 'item';

export function filtersFromParams(params: URLSearchParams): ApprovalFilters {
  const status = params.get('status');
  const type = params.get('type');
  const assignee = params.get('assignee');
  const priority = params.get('priority');
  const sort = params.get('sort');
  return {
    status: STATUS_FILTERS.includes(status as ApprovalStatusFilter)
      ? (status as ApprovalStatusFilter)
      : DEFAULT_FILTERS.status,
    type: type && APPROVAL_TYPES.includes(type) ? type : null,
    assignee: ASSIGNEE_FILTERS.includes(assignee as ApprovalAssigneeFilter)
      ? (assignee as ApprovalAssigneeFilter)
      : DEFAULT_FILTERS.assignee,
    priority: PRIORITY_FILTERS.includes(priority as ApprovalPriority)
      ? (priority as ApprovalPriority)
      : DEFAULT_FILTERS.priority,
    sort: SORTS.includes(sort as ApprovalSort)
      ? (sort as ApprovalSort)
      : DEFAULT_FILTERS.sort,
  };
}

/** Whether any filter differs from the default (A2: "Clear filters"). */
export function isFiltered(filters: ApprovalFilters): boolean {
  return (Object.keys(DEFAULT_FILTERS) as (keyof ApprovalFilters)[]).some(
    (key) => filters[key] !== DEFAULT_FILTERS[key],
  );
}

/** The 0-based offset of `?page=N` (1 when missing or not a positive int). */
export function offsetFromParams(params: URLSearchParams): number {
  const page = Number(params.get('page'));
  return Number.isInteger(page) && page > 1
    ? (page - 1) * APPROVALS_PAGE_SIZE
    : 0;
}

/** The URL query of `filters` at `offset` (defaults left out). */
export function paramsFor(
  filters: ApprovalFilters,
  offset = 0,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.status !== DEFAULT_FILTERS.status) {
    params.set('status', filters.status);
  }
  if (filters.type) {
    params.set('type', filters.type);
  }
  if (filters.assignee !== DEFAULT_FILTERS.assignee) {
    params.set('assignee', filters.assignee);
  }
  if (filters.priority) {
    params.set('priority', filters.priority);
  }
  if (filters.sort !== DEFAULT_FILTERS.sort) {
    params.set('sort', filters.sort);
  }
  const page = Math.floor(offset / APPROVALS_PAGE_SIZE) + 1;
  if (page > 1) {
    params.set('page', String(page));
  }
  return params;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The open panel's request id (`?item=`), or null; a value that is not a
 * UUID is returned as is, so the panel shows its not-found state. */
export function itemFromParams(params: URLSearchParams): string | null {
  const item = params.get(ITEM_PARAM);
  return item ? item : null;
}

/** Whether `id` can be a request id (the API answers 422 otherwise). */
export function isRequestId(id: string): boolean {
  return UUID.test(id);
}

/** `params` with the panel's `item` set, or removed for null. */
export function withItem(
  params: URLSearchParams,
  id: string | null,
): URLSearchParams {
  const next = new URLSearchParams(params);
  if (id) {
    next.set(ITEM_PARAM, id);
  } else {
    next.delete(ITEM_PARAM);
  }
  return next;
}
