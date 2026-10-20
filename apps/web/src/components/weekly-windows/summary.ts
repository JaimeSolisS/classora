import type { TFunction } from 'i18next';
import { summaryGroups, type WeeklyWindow } from './windows';

/**
 * The one-line summary of a room's (or a tutor's) weekly windows, e.g.
 * "Lun–Vie 09:00–20:00; Sáb 09:00–14:00", or "Sin restricción" / "No
 * restriction" without windows.
 */
export function windowsSummary(
  windows: readonly WeeklyWindow[],
  t: TFunction,
): string {
  const groups = summaryGroups(windows);
  if (groups.length === 0) {
    return t('weeklyWindows.unrestricted');
  }
  return groups
    .map((group) => {
      const first = t(`weeklyWindows.weekdaysShort.${group.firstWeekday}`);
      const days =
        group.firstWeekday === group.lastWeekday
          ? first
          : t('weeklyWindows.range', {
              start: first,
              end: t(`weeklyWindows.weekdaysShort.${group.lastWeekday}`),
            });
      const ranges = group.ranges
        .map(([start, end]) => t('weeklyWindows.range', { start, end }))
        .join(', ');
      return `${days} ${ranges}`;
    })
    .join('; ');
}
