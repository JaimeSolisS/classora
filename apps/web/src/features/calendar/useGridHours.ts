import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import {
  SETTINGS_QUERY_KEY,
  type fetchSettings,
} from '@/features/settings/api';
import type { LocalDate } from '@/lib/time';
import { businessHoursOf, visibleHours, type TimedItem } from './hours';
import type { CalendarView } from './views';

// The opening hours of both time grids (HF-08): the staff calendar and "My
// schedule". The business settings query is the Settings page's
// (SETTINGS_QUERY_KEY), so a saved change shows here with no reload. The
// grid mounts once it has settled (`ready`), success or failure, so it
// never jumps from 24 hours to the opening hours; a failure falls back to
// the full day, without an alert.

export type FetchSettings = typeof fetchSettings;

export function useGridHours(
  load: FetchSettings,
  view: CalendarView,
  date: LocalDate,
  items: readonly TimedItem[],
  timeZone: string,
) {
  const settings = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: ({ signal }) => load(signal),
    retry: false,
  });
  const hours = settings.data?.operating_hours ?? null;
  const grid = useMemo(
    () => visibleHours(hours, view, date, items, timeZone),
    [hours, view, date, items, timeZone],
  );
  const businessHours = useMemo(() => businessHoursOf(hours), [hours]);
  return { ready: !settings.isPending, grid, businessHours };
}
