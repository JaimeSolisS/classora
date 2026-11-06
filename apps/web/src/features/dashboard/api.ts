import type { components } from '@classora/api-client';
import { useQuery } from '@tanstack/react-query';
import { api, apiCall, ApiError } from '@/app/api';

// The caller's dashboard layout (M8-10, #127 C; `_docs/api.md` "Dashboard
// layout"): exactly the widgets available to them, each once, in order,
// each shown or hidden. It holds no figure: each widget reads its own KPI
// route. Reset is `DELETE`. No Idempotency-Key (`PUT` is idempotent).

export type WidgetCode = components['schemas']['WidgetCode'];
export type LayoutEntry = components['schemas']['DashboardWidget'];
export type DashboardLayout = components['schemas']['DashboardLayoutResponse'];

export const LAYOUT_KEY = ['dashboard', 'layout'] as const;

export function fetchLayout(signal: AbortSignal): Promise<DashboardLayout> {
  return apiCall(
    () => api.GET('/api/v1/reports/dashboard/layout', { signal }),
    { signal },
  );
}

export function saveLayout(
  widgets: readonly LayoutEntry[],
): Promise<DashboardLayout> {
  return apiCall(() =>
    api.PUT('/api/v1/reports/dashboard/layout', {
      body: {
        widgets: widgets.map(({ widget, visible }) => ({ widget, visible })),
      },
    }),
  );
}

export async function resetLayout(): Promise<void> {
  await apiCall(() => api.DELETE('/api/v1/reports/dashboard/layout'));
}

export function useDashboardLayout(enabled = true) {
  return useQuery({
    queryKey: LAYOUT_KEY,
    queryFn: ({ signal }) => fetchLayout(signal),
    enabled,
    // A 403 means no widget is available (a stale permission cache).
    meta: { expectedStatuses: [403] },
  });
}

/** `422 dashboard_widget_unavailable`: the caller's permissions changed. */
export function isWidgetUnavailable(error: unknown): boolean {
  return (
    error instanceof ApiError && error.code === 'dashboard_widget_unavailable'
  );
}
