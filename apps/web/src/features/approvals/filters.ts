import {
  APPROVAL_TYPES,
  APPROVALS_PAGE_SIZE,
  ASSIGNEE_FILTERS,
  DEFAULT_FILTERS,
  STATUS_FILTERS,
  type ApprovalAssigneeFilter,
  type ApprovalFilters,
  type ApprovalStatusFilter,
} from './api';

// The Approvals page's filters and page live in the URL (#40 F3):
// ?status=…&type=…&assignee=…&page=N. A missing or unknown value means the
// default, so an old or hand-edited link still opens the page.

export function filtersFromParams(params: URLSearchParams): ApprovalFilters {
  const status = params.get('status');
  const type = params.get('type');
  const assignee = params.get('assignee');
  return {
    status: STATUS_FILTERS.includes(status as ApprovalStatusFilter)
      ? (status as ApprovalStatusFilter)
      : DEFAULT_FILTERS.status,
    type: type && APPROVAL_TYPES.includes(type) ? type : null,
    assignee: ASSIGNEE_FILTERS.includes(assignee as ApprovalAssigneeFilter)
      ? (assignee as ApprovalAssigneeFilter)
      : DEFAULT_FILTERS.assignee,
  };
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
  const page = Math.floor(offset / APPROVALS_PAGE_SIZE) + 1;
  if (page > 1) {
    params.set('page', String(page));
  }
  return params;
}
