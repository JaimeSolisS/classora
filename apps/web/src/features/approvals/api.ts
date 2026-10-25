import type { components } from '@classora/api-client';
import type { ParseKeys } from 'i18next';
import { api, apiCall } from '@/app/api';
import type { PermissionCode } from '@/features/auth/session';

// The approval inbox calls (#40 F). The list is a query; the decisions are
// direct calls (their answer only refreshes the list).

export type ApprovalRequestItem = components['schemas']['ApprovalRequestItem'];
export type ApprovalRequestPage =
  components['schemas']['OffsetPage_ApprovalRequestItem_'];
export type ApprovalAction = components['schemas']['ApprovalAction'];
export type ApprovalStatus = components['schemas']['ApprovalStatus'];
export type ApprovalPriority = components['schemas']['ApprovalPriority'];
export type ApprovalStatusFilter =
  components['schemas']['ApprovalStatusFilter'];
export type ApprovalAssigneeFilter =
  components['schemas']['ApprovalAssigneeFilter'];

/**
 * The permissions that review at least one approval type: the Approvals page
 * and its navigation item need one of them. Each new type adds its own
 * review permission here.
 */
export const APPROVAL_REVIEW_PERMISSIONS: readonly PermissionCode[] = [
  'registration.review',
  'qualification.review',
  // #52 E2: the reassignment items of declined or expired offers.
  'schedule.assign',
  // #53 occurrence conflicts and #59 session requests.
  'schedule.manage',
  // #63 availability changes that affect scheduled classes.
  'tutor.manage',
  // #77 session closeouts (Owner, Administrator, Academic Coordinator).
  'teaching_hours.approve',
];

/** The approval types the page knows, with their label key. */
export const APPROVAL_TYPE_LABELS: Readonly<
  Record<string, ParseKeys<'common'>>
> = {
  'identity.registration': 'approvals.types.identity_registration',
  'staff.qualification': 'approvals.types.staff_qualification',
  'scheduling.offer_reassignment':
    'approvals.types.scheduling_offer_reassignment',
  'scheduling.occurrence_conflict':
    'approvals.types.scheduling_occurrence_conflict',
  'scheduling.session_request': 'approvals.types.scheduling_session_request',
  'scheduling.change_request': 'approvals.types.scheduling_change_request',
  'staff.availability_change': 'approvals.types.staff_availability_change',
  'academics.session_closeout': 'approvals.types.academics_session_closeout',
};

export const APPROVAL_TYPES: readonly string[] =
  Object.keys(APPROVAL_TYPE_LABELS);

/** The status filter's choices (F3), Open first (the default). */
export const STATUS_FILTERS: readonly ApprovalStatusFilter[] = [
  'open',
  'approved',
  'rejected',
  'cancelled',
  'all',
];

export const ASSIGNEE_FILTERS: readonly ApprovalAssigneeFilter[] = [
  'any',
  'me',
  'unassigned',
];

export interface ApprovalFilters {
  status: ApprovalStatusFilter;
  /** One type, or null for every type the user reviews. */
  type: string | null;
  assignee: ApprovalAssigneeFilter;
}

export const DEFAULT_FILTERS: ApprovalFilters = {
  status: 'open',
  type: null,
  assignee: 'any',
};

export const APPROVALS_QUERY_KEY = ['approval-requests'] as const;

/** Rows per page of the list. */
export const APPROVALS_PAGE_SIZE = 10;

export function approvalsKey(filters: ApprovalFilters, offset: number) {
  return [...APPROVALS_QUERY_KEY, 'list', { ...filters, offset }] as const;
}

export function fetchApprovals(
  filters: ApprovalFilters,
  offset: number,
  signal: AbortSignal,
): Promise<ApprovalRequestPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/approval-requests', {
        params: {
          query: {
            status: filters.status,
            assignee: filters.assignee,
            ...(filters.type ? { request_type: filters.type } : {}),
            limit: APPROVALS_PAGE_SIZE,
            offset,
          },
        },
        signal,
      }),
    { signal },
  );
}

/**
 * Approve, reject or request changes. `reason` is sent (trimmed) only by
 * reject and request changes; an empty one is left out.
 */
export function decideApproval(
  id: string,
  action: ApprovalAction,
  reason?: string,
): Promise<ApprovalRequestItem> {
  const params = { path: { request_id: id } };
  const trimmed = reason?.trim();
  const body = trimmed ? { reason: trimmed } : {};
  return apiCall(() => {
    switch (action) {
      case 'approve':
        return api.POST('/api/v1/approval-requests/{request_id}/approve', {
          params,
        });
      case 'reject':
        return api.POST('/api/v1/approval-requests/{request_id}/reject', {
          params,
          body,
        });
      case 'request_changes':
        return api.POST(
          '/api/v1/approval-requests/{request_id}/request-changes',
          { params, body },
        );
    }
  });
}

/**
 * The types whose rejection needs a reason (the handler's
 * `reason_required_on_reject`).
 */
export const REJECT_REASON_REQUIRED: ReadonlySet<string> = new Set<string>([
  'staff.qualification',
  'scheduling.offer_reassignment',
  'scheduling.occurrence_conflict',
  'scheduling.session_request',
  'scheduling.change_request',
  'staff.availability_change',
]);

/** Whether `action` on an item of `type` needs a reason (UX only; the API
 * decides): request changes always, reject for some types. */
export function reasonRequired(type: string, action: ApprovalAction): boolean {
  return (
    action === 'request_changes' ||
    (action === 'reject' && REJECT_REASON_REQUIRED.has(type))
  );
}
