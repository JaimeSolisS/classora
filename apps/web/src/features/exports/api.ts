import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The export calls (M8-07b; the API is #124, `_docs/api.md` "Exports").
// Every route answers only the caller's own exports. A personal dataset's
// request may answer `403 reauth_required`: apiCall opens the password
// dialog and resends it once (E3). The file is a plain same-origin link
// (E2, the payslip pattern); no bytes pass through this module.

export type ExportDataset = components['schemas']['ExportDatasetItem'];
export type ExportColumn = components['schemas']['ExportColumnItem'];
export type ExportJob = components['schemas']['ExportJobDetail'];
export type ExportJobPage =
  components['schemas']['OffsetPage_ExportJobDetail_'];
export type ExportJobStatus = components['schemas']['ExportJobStatus'];
export type ExportErrorCode = components['schemas']['ExportErrorCode'];
export type ExportFormat = components['schemas']['ExportFormat'];
export type ExportParameters =
  | components['schemas']['StudentsExportParameters']
  | components['schemas']['ClassSessionsExportParameters']
  | components['schemas']['ExpensesExportParameters'];

export const EXPORT_FORMATS: readonly ExportFormat[] = ['csv', 'xlsx'];

/** The history rows per page. */
export const EXPORTS_PAGE_SIZE = 20;
/** E2: poll every 3 s while an export is queued… */
export const POLL_INTERVAL_MS = 3_000;
/** …for at most 10 minutes. */
export const POLL_TIMEOUT_MS = 10 * 60 * 1_000;

export const EXPORTS_QUERY_KEY = ['exports'] as const;
export const EXPORT_DATASETS_KEY = [...EXPORTS_QUERY_KEY, 'datasets'] as const;
export const EXPORTS_LIST_KEY = [...EXPORTS_QUERY_KEY, 'list'] as const;

export function exportsListKey(page: number) {
  return [...EXPORTS_LIST_KEY, page] as const;
}

export function fetchExportDatasets(
  signal: AbortSignal,
): Promise<components['schemas']['ExportDatasetList']> {
  return apiCall(() => api.GET('/api/v1/exports/datasets', { signal }), {
    signal,
  });
}

export function fetchExports(
  page: number,
  signal: AbortSignal,
): Promise<ExportJobPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/exports', {
        params: {
          query: {
            limit: EXPORTS_PAGE_SIZE,
            offset: (page - 1) * EXPORTS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function requestExport(body: ExportParameters): Promise<ExportJob> {
  return apiCall(() => api.POST('/api/v1/exports', { body }));
}

/** The file of a ready export (requester only; `Cache-Control: no-store`). */
export function exportDownloadUrl(exportId: string): string {
  return `/api/v1/exports/${encodeURIComponent(exportId)}/download`;
}
