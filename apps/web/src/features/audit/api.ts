import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { localDayBounds } from '@/lib/time';
import type { AuditFilters } from './filters';

// The audit log calls (#120; GET /api/v1/audit-events, #22): read only, for
// audit.view holders (Owner, Administrator). The list is newest first in
// cursor pages; the record types feed the "Record type" select (B4).

export type AuditEvent = components['schemas']['AuditEventListItem'];
export type AuditEventPage =
  components['schemas']['CursorPage_AuditEventListItem_'];
export type AuditAccount = components['schemas']['AccountName'];

/** The automatic actions (translated, E4); any other action is a code. */
export const AUTOMATIC_ACTIONS = [
  'create',
  'update',
  'delete',
  'soft_delete',
  'restore',
] as const;
export type AutomaticAction = (typeof AUTOMATIC_ACTIONS)[number];

export function isAutomaticAction(action: string): action is AutomaticAction {
  return (AUTOMATIC_ACTIONS as readonly string[]).includes(action);
}

/** The value the API writes for a masked column (`app.core.audit.MASKED`). */
export const MASKED = '[masked]';

/** Events per page (C3). */
export const AUDIT_PAGE_SIZE = 25;

export const AUDIT_QUERY_KEY = ['audit-events'] as const;
export const RECORD_TYPES_KEY = [...AUDIT_QUERY_KEY, 'record-types'] as const;

export function auditEventsKey(filters: AuditFilters, timeZone: string) {
  return [...AUDIT_QUERY_KEY, 'list', { ...filters, timeZone }] as const;
}

/**
 * The query of `filters`: the center-local "from" day starts the range and
 * the day after "to" ends it (exclusive, E5).
 */
export function auditQuery(filters: AuditFilters, timeZone: string) {
  return {
    ...(filters.recordType && filters.recordId
      ? { record_type: filters.recordType, record_id: filters.recordId }
      : {}),
    ...(filters.actorId ? { actor_id: filters.actorId } : {}),
    ...(filters.from
      ? { occurred_from: localDayBounds(filters.from, timeZone).start }
      : {}),
    ...(filters.to
      ? { occurred_to: localDayBounds(filters.to, timeZone).end }
      : {}),
  };
}

export function fetchAuditEvents(
  filters: AuditFilters,
  timeZone: string,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<AuditEventPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/audit-events', {
        params: {
          query: {
            ...auditQuery(filters, timeZone),
            limit: AUDIT_PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchRecordTypes(signal?: AbortSignal): Promise<string[]> {
  return apiCall(
    () => api.GET('/api/v1/audit-events/record-types', { signal }),
    { signal },
  ).then((body) => body.items);
}

/** The first 8 characters of an id, for display next to a name. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}
