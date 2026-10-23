import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
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
// Week on wide screens and List below 768 px (#57 D2).

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
      <h1 className="text-2xl font-semibold">
        {t('scheduling.calendar.title')}
      </h1>
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
