import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The recently deleted records (#129 B; GET /api/v1/deleted-records, POST
// /api/v1/deleted-records/{kind}/{record_id}/restore). The list holds only
// the caller's own records they may still restore, newest deletion first,
// at most 100; it never carries a record's content.

export type DeletedRecord = components['schemas']['DeletedRecordItem'];
export type DeletedRecordList = components['schemas']['DeletedRecordList'];
export type DeletedRecordKind = components['schemas']['DeletedRecordKind'];
export type RestoredRecord = components['schemas']['RestoredRecord'];

export const DELETED_RECORDS_KEY = ['deleted-records'] as const;

export function fetchDeletedRecords(
  signal?: AbortSignal,
): Promise<DeletedRecordList> {
  return apiCall(() => api.GET('/api/v1/deleted-records', { signal }), {
    signal,
  });
}

export function restoreDeletedRecord(
  record: Pick<DeletedRecord, 'kind' | 'id' | 'version'>,
): Promise<RestoredRecord> {
  return apiCall(() =>
    api.POST('/api/v1/deleted-records/{kind}/{record_id}/restore', {
      params: { path: { kind: record.kind, record_id: record.id } },
      body: { version: record.version },
    }),
  );
}

/**
 * The record's existing page, opened from the "restored" message. A class
 * note has no page of its own (it lives on its session's notes page, and
 * the list doesn't name the session), so it gets no link.
 */
export function recordPath(kind: DeletedRecordKind, id: string): string | null {
  switch (kind) {
    case 'evaluation':
      return `/evaluations/${id}`;
    case 'learning_plan':
      return `/learning-plans/${id}`;
    case 'assignment':
      return `/assignments/${id}`;
    case 'progress_report':
      return `/progress-reports/${id}`;
    default:
      return null;
  }
}
