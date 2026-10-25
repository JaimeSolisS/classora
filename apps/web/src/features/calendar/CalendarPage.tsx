import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import {
  fetchOverdueCloseouts,
  OVERDUE_CLOSEOUTS_KEY,
} from '@/features/scheduling/overdue/api';
import { API_LOADERS } from './api';
import { ScheduleCalendar } from './ScheduleCalendar';
import {
  calendarSearchParams,
  parseCalendarState,
  type CalendarState,
} from './urlState';
import { WIDE_VIEWPORT_PX, type CalendarView } from './views';

// /schedule, "Schedule" (#57 D1), for schedule.view holders (Owner,
// Administrator, Coordinator, Finance). Loaded lazily (the router), so
// FullCalendar and its Temporal polyfill stay out of the entry chunk. The
// URL holds the view, the date and the filters (#57 D3); the default view is
// Week on wide screens and List below 768 px (#57 D2). #301b B: academic
// reviewers (teaching_hours.approve) get "Overdue closeouts (n)" next to the
// heading, counted by one limit=1 read; nobody else sends it.

const COUNT_QUERY = { limit: 1, offset: 0 } as const;

function OverdueLink() {
  const { t } = useTranslation();
  const count = useQuery({
    queryKey: [...OVERDUE_CLOSEOUTS_KEY, 'count'],
    queryFn: ({ signal }) => fetchOverdueCloseouts(COUNT_QUERY, signal),
    retry: false,
  });
  return (
    <Link
      to="/schedule/overdue-closeouts"
      className="text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      {count.data
        ? t('scheduling.overdue.linkCount', { total: count.data.total })
        : t('scheduling.overdue.link')}
    </Link>
  );
}

function defaultView(): CalendarView {
  return typeof window.matchMedia !== 'function' ||
    window.matchMedia(`(min-width: ${WIDE_VIEWPORT_PX}px)`).matches
    ? 'week'
    : 'list';
}

export function CalendarPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.calendar.title'));
  const timeZone = useCenterTimeZone();
  const canSchedule = useCan('schedule.manage');
  const canOverride = useCan('schedule.override');
  const canReviewHours = useCan('teaching_hours.approve');
  const [params, setParams] = useSearchParams();
  const today = todayInZone(timeZone);
  const state = useMemo(
    () => parseCalendarState(params, { view: defaultView(), date: today }),
    [params, today],
  );

  const onStateChange = (next: CalendarState) =>
    setParams(calendarSearchParams(next));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h1 className="text-2xl font-semibold">
          {t('scheduling.calendar.title')}
        </h1>
        {canReviewHours ? <OverdueLink /> : null}
      </div>
      <ScheduleCalendar
        state={state}
        onStateChange={onStateChange}
        timeZone={timeZone}
        today={today}
        loaders={API_LOADERS}
        canSchedule={canSchedule}
        canOverride={canOverride}
      />
    </div>
  );
}

export default CalendarPage;
