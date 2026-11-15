import {
  isCalendarView,
  WIDE_VIEWPORT_PX,
  type CalendarView,
} from '@/features/calendar/views';
import { parseLocalDate, type LocalDate } from '@/lib/time';

// The state of "My schedule" in the URL (#67 E2, E4; the #57 D3 rules):
// `?for=` (whose schedule), `?view=` and `?date=` (a local date in the center
// zone). Invalid values take their defaults without an error. Pure.

export interface MyScheduleState {
  /** The requested entry key (`teaching` or a student id), unchecked. */
  whose: string | null;
  view: CalendarView;
  date: LocalDate;
}

function dateParam(value: string | null): LocalDate | null {
  if (value === null) {
    return null;
  }
  try {
    return parseLocalDate(value);
  } catch {
    return null;
  }
}

/** The default view: List below 768 px, Week above (#67 E4). */
export function defaultView(width?: number): CalendarView {
  if (width !== undefined) {
    return width >= WIDE_VIEWPORT_PX ? 'week' : 'list';
  }
  return typeof window.matchMedia !== 'function' ||
    window.matchMedia(`(min-width: ${WIDE_VIEWPORT_PX}px)`).matches
    ? 'week'
    : 'list';
}

export function parseMyScheduleState(
  params: URLSearchParams,
  defaults: { view: CalendarView; date: LocalDate },
): MyScheduleState {
  const view = params.get('view');
  return {
    whose: params.get('for'),
    view: isCalendarView(view) ? view : defaults.view,
    date: dateParam(params.get('date')) ?? defaults.date,
  };
}

export function myScheduleSearchParams(
  state: MyScheduleState,
): URLSearchParams {
  const params = new URLSearchParams();
  if (state.whose) params.set('for', state.whose);
  params.set('view', state.view);
  params.set('date', state.date);
  return params;
}
