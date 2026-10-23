import classicThemePlugin from '@fullcalendar/react/themes/classic';
import dayGridPlugin from '@fullcalendar/react/daygrid';
import interactionPlugin from '@fullcalendar/react/interaction';
import listPlugin from '@fullcalendar/react/list';
import timeGridPlugin from '@fullcalendar/react/timegrid';

// The calendar's views (#57 C2, ADR-0005 D8): only FullCalendar's standard
// time-grid day and week, day-grid month and list views, plus the classic
// theme and the interaction code shipped in the pinned @fullcalendar/react
// (#58 D1: dragging and resizing events; no selecting, no date-click).

export const CALENDAR_VIEWS = {
  day: 'timeGridDay',
  week: 'timeGridWeek',
  month: 'dayGridMonth',
  list: 'listWeek',
} as const;

export type CalendarView = keyof typeof CALENDAR_VIEWS;

/** The view buttons, in toolbar order. */
export const VIEW_ORDER: readonly CalendarView[] = [
  'day',
  'week',
  'month',
  'list',
];

export const CALENDAR_PLUGINS = [
  dayGridPlugin,
  timeGridPlugin,
  listPlugin,
  classicThemePlugin,
  interactionPlugin,
];

/** The viewport width from which the week view is the default (#57 D2). */
export const WIDE_VIEWPORT_PX = 768;

export function isCalendarView(value: string | null): value is CalendarView {
  return value !== null && Object.hasOwn(CALENDAR_VIEWS, value);
}
