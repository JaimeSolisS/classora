import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The calls of the weekly series (#54b): the create (#53), the read (shared
// with the session page, session/api.ts), moving one occurrence (PATCH
// /class-sessions/{id}), and editing or cancelling the series from a date
// (#54), and overriding one `conflict` occurrence (#236). The web never
// sends or parses RRULE text (ADR-0005 D4).

export type ClassSeriesCreate = components['schemas']['ClassSeriesCreate'];
export type ClassSeriesCreated = components['schemas']['ClassSeriesCreated'];
export type ClassSeriesEdit = components['schemas']['ClassSeriesEdit'];
export type ClassSeriesEdited = components['schemas']['ClassSeriesEdited'];
export type ClassSeriesCancel = components['schemas']['ClassSeriesCancel'];
export type ClassSeriesCancelled =
  components['schemas']['ClassSeriesCancelled'];
export type ClassSessionUpdate = components['schemas']['ClassSessionUpdate'];
export type ClassSessionUpdated = components['schemas']['ClassSessionUpdated'];
export type SeriesOccurrence = components['schemas']['SeriesOccurrence'];
export type SeriesRecurrence = components['schemas']['SeriesRecurrence'];
export type SeriesEditMode = components['schemas']['SeriesEditMode'];
export type SeriesEditSummary = components['schemas']['SeriesEditSummary'];

export function createClassSeries(
  body: ClassSeriesCreate,
): Promise<ClassSeriesCreated> {
  return apiCall(() => api.POST('/api/v1/class-series', { body }));
}

export function updateClassSession(
  id: string,
  body: ClassSessionUpdate,
): Promise<ClassSessionUpdated> {
  return apiCall(() =>
    api.PATCH('/api/v1/class-sessions/{class_session_id}', {
      params: { path: { class_session_id: id } },
      body,
    }),
  );
}

export function editClassSeries(
  id: string,
  body: ClassSeriesEdit,
): Promise<ClassSeriesEdited> {
  return apiCall(() =>
    api.POST('/api/v1/class-series/{class_series_id}/edits', {
      params: { path: { class_series_id: id } },
      body,
    }),
  );
}

/** #236 G3: override one `conflict` occurrence's soft codes and schedule it. */
export function overrideConflictOccurrence(
  id: string,
  body: components['schemas']['ConflictOverrideCreate'],
): Promise<components['schemas']['ClassSessionDetail']> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/{class_session_id}/conflict-overrides', {
      params: { path: { class_session_id: id } },
      body,
    }),
  );
}

export function cancelClassSeries(
  id: string,
  body: ClassSeriesCancel,
): Promise<ClassSeriesCancelled> {
  return apiCall(() =>
    api.POST('/api/v1/class-series/{class_series_id}/cancellations', {
      params: { path: { class_series_id: id } },
      body,
    }),
  );
}
