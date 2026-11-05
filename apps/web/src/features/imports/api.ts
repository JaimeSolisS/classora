import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The import calls (M8-05b; the API is #122, `_docs/api.md` "Imports"). No
// file is ever stored (owner decision 2026-10-21, E2): the browser reads the
// CSV and posts its text in a JSON body. Every route answers only the kinds
// the caller may import; another kind's job is a 404.

export type ImportJob = components['schemas']['ImportJobDetail'];
export type ImportJobStatus = components['schemas']['ImportJobStatus'];
export type ImportJobPage =
  components['schemas']['OffsetPage_ImportJobDetail_'];
export type ImportRow = components['schemas']['ImportRowItem'];
export type ImportRowStatus = components['schemas']['ImportRowStatus'];
export type ImportRowPage = components['schemas']['OffsetPage_ImportRowItem_'];
export type ImportRowIssue = components['schemas']['ImportRowIssue'];

export const JOB_STATUSES: readonly ImportJobStatus[] = [
  'staged',
  'committing',
  'committed',
  'failed',
  'discarded',
  'expired',
];

/** The API's body cap (2 MiB): bigger files are refused before sending. */
export const MAX_FILE_BYTES = 2 * 1024 * 1024;
/** The history rows per page. */
export const IMPORTS_PAGE_SIZE = 25;
/** The report rows per page (the API allows at most 100). */
export const ROWS_PAGE_SIZE = 50;
/** E3: poll every 3 s while committing… */
export const POLL_INTERVAL_MS = 3_000;
/** …for at most 10 minutes. */
export const POLL_TIMEOUT_MS = 10 * 60 * 1_000;

export const IMPORTS_QUERY_KEY = ['imports'] as const;
export const IMPORTS_LIST_KEY = [...IMPORTS_QUERY_KEY, 'list'] as const;

export interface ImportListParams {
  /** A kind code, or '' for every kind. */
  kind: string;
  /** A job status, or '' for every status. */
  status: ImportJobStatus | '';
  /** 1-based page number. */
  page: number;
}

export interface ImportRowParams {
  status: ImportRowStatus | '';
  page: number;
}

export function importsListKey(params: ImportListParams) {
  return [...IMPORTS_LIST_KEY, params] as const;
}

export function importKey(importId: string) {
  return [...IMPORTS_QUERY_KEY, 'detail', importId] as const;
}

/** The rows of one version of the job: a new version refetches them. */
export function importRowsKey(
  importId: string,
  version: number,
  params: ImportRowParams,
) {
  return [...IMPORTS_QUERY_KEY, 'rows', importId, version, params] as const;
}

export function fetchImports(
  { kind, status, page }: ImportListParams,
  signal: AbortSignal,
): Promise<ImportJobPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/imports', {
        params: {
          query: {
            ...(kind ? { kind } : {}),
            ...(status ? { status } : {}),
            limit: IMPORTS_PAGE_SIZE,
            offset: (page - 1) * IMPORTS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchImport(
  importId: string,
  signal: AbortSignal,
): Promise<ImportJob> {
  return apiCall(
    () =>
      api.GET('/api/v1/imports/{import_id}', {
        params: { path: { import_id: importId } },
        signal,
      }),
    { signal },
  );
}

export function fetchImportRows(
  importId: string,
  { status, page }: ImportRowParams,
  signal: AbortSignal,
): Promise<ImportRowPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/imports/{import_id}/rows', {
        params: {
          path: { import_id: importId },
          query: {
            ...(status ? { status } : {}),
            limit: ROWS_PAGE_SIZE,
            offset: (page - 1) * ROWS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function createImport(body: {
  kind: string;
  file_name: string | null;
  csv: string;
}): Promise<ImportJob> {
  return apiCall(() => api.POST('/api/v1/imports', { body }));
}

export function commitImport(
  importId: string,
  version: number,
): Promise<ImportJob> {
  return apiCall(() =>
    api.POST('/api/v1/imports/{import_id}/commit', {
      params: { path: { import_id: importId } },
      body: { version },
    }),
  );
}

export function discardImport(
  importId: string,
  version: number,
): Promise<ImportJob> {
  return apiCall(() =>
    api.POST('/api/v1/imports/{import_id}/discard', {
      params: { path: { import_id: importId } },
      body: { version },
    }),
  );
}
