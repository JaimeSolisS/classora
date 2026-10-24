import FullCalendar, {
  useCalendarController,
  type CalendarOptions,
} from '@fullcalendar/react';
import esLocale from '@fullcalendar/react/locales/es';
import '@fullcalendar/react/skeleton.css';
import '@fullcalendar/react/themes/classic/theme.css';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import '@/features/calendar/calendar.css';
import {
  rangeInstants,
  shiftDate,
  type LocalRange,
} from '@/features/calendar/urlState';
import {
  CALENDAR_PLUGINS,
  CALENDAR_VIEWS,
  VIEW_ORDER,
  type CalendarView,
} from '@/features/calendar/views';
import { useLanguage } from '@/i18n/use-language';
import { parseLocalDate, type LocalDate } from '@/lib/time';
import {
  MY_SCHEDULE_KEY,
  type RangeQuery,
  type ScheduleEntry,
  type SchedulePage,
} from './api';
import { EntryContent } from './EntryContent';
import { EntryDialog } from './EntryDialog';
import type { LabelContext } from './labels';

// The read-only calendar of "My schedule" (#67 E4-E7): the #57 views
// (FullCalendar v7, ADR-0005 D8: List, Day, Week, Month) in the center zone,
// Monday first, with the #57 toolbar. No drag, select or date-click (#58
// edits the staff calendar only). One query per (whose, range); the
// details dialog shows the loaded item and the page's action for it
// (`renderAction`, #62b A1).

export interface CalendarPosition {
  view: CalendarView;
  date: LocalDate;
}

export interface MyScheduleCalendarProps {
  /** The query key part naming whose schedule this is. */
  whoseKey: string;
  /** Reads the schedule of one range. */
  load: (query: RangeQuery, signal: AbortSignal) => Promise<SchedulePage>;
  position: CalendarPosition;
  onPositionChange: (position: CalendarPosition) => void;
  timeZone: string;
  today: LocalDate;
  /** Renders an error of the read (the consent case is the page's). */
  renderError?: (error: unknown, retry: () => void) => React.ReactNode;
  /** The details dialog's action for an item (#62b A1). */
  renderAction?: (entry: ScheduleEntry) => React.ReactNode;
}

interface EventProps {
  entry: ScheduleEntry;
}

const LOCALES = [esLocale];

const SLOT_FORMATS = {
  es: { hour: 'numeric', minute: '2-digit', hour12: false },
  en: { hour: 'numeric', minute: '2-digit', hour12: true },
} as const;

const VIEW_OPTIONS = {
  timeGridDay: {
    titleFormat: { year: 'numeric', month: 'long', day: 'numeric' },
  },
  timeGridWeek: {
    titleFormat: { year: 'numeric', month: 'short', day: 'numeric' },
  },
} as const;

function localDateOf(edge: string): LocalDate {
  return parseLocalDate(edge.slice(0, 10));
}

export function MyScheduleCalendar({
  whoseKey,
  load,
  position,
  onPositionChange,
  timeZone,
  today,
  renderError,
  renderAction,
}: MyScheduleCalendarProps) {
  const { t } = useTranslation();
  const { language } = useLanguage();
  const id = useId();
  const controller = useCalendarController();
  const [range, setRange] = useState<LocalRange | null>(null);
  const [selected, setSelected] = useState<ScheduleEntry | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const returnId = useRef<string | null>(null);
  const region = useRef<HTMLDivElement | null>(null);
  const [initial] = useState(() => position);

  // Keep FullCalendar on the URL's view and date.
  useEffect(() => {
    if (!controller.view) {
      return;
    }
    if (controller.view.type !== CALENDAR_VIEWS[position.view]) {
      controller.changeView(CALENDAR_VIEWS[position.view]);
    }
    controller.gotoDate(position.date);
  }, [controller, position.view, position.date]);

  const query: RangeQuery | null = range
    ? rangeInstants(range, timeZone)
    : null;
  const schedule = useQuery({
    queryKey: [...MY_SCHEDULE_KEY, 'schedule', whoseKey, query],
    queryFn: ({ signal }) => load(query!, signal),
    enabled: query !== null,
    retry: false,
  });
  const entries = useMemo(() => schedule.data?.entries ?? [], [schedule.data]);

  const context: LabelContext = useMemo(
    () => ({ t, timeZone, language }),
    [t, timeZone, language],
  );

  const events = useMemo(
    () =>
      entries.map((entry) => ({
        id: entry.item.id,
        start: entry.item.starts_at,
        end: entry.item.ends_at,
        title: entry.item.subject.name,
        editable: false,
        extendedProps: { entry } satisfies EventProps,
      })),
    [entries],
  );

  const options = useMemo<CalendarOptions>(
    () => ({
      plugins: CALENDAR_PLUGINS,
      initialView: CALENDAR_VIEWS[initial.view],
      initialDate: initial.date,
      timeZone,
      locales: LOCALES,
      locale: language,
      firstDay: 1,
      headerToolbar: false,
      height: 'auto',
      allDaySlot: false,
      nowIndicator: true,
      scrollTime: '07:00:00',
      slotHeaderFormat: SLOT_FORMATS[language],
      slotMinHeight: 36,
      views: VIEW_OPTIONS,
      eventColor: 'var(--card)',
      eventContrastColor: 'var(--card-foreground)',
      noEventsText: t('mySchedule.empty'),
      eventInteractive: true,
      editable: false,
      selectable: false,
      eventStartEditable: false,
      eventDurationEditable: false,
      eventMinHeight: 22,
      eventClass: (info) => {
        const { entry } = info.event.extendedProps as EventProps;
        return ['cl-event', `cl-event--${entry.item.state}`].join(' ');
      },
      eventContent: (info) => {
        const { entry } = info.event.extendedProps as EventProps;
        return (
          <EntryContent
            entry={entry}
            context={context}
            compact={info.view.type.startsWith('timeGrid')}
          />
        );
      },
      eventDidMount: (info) => {
        info.el.dataset.sessionId = info.event.id;
      },
      eventClick: (info) => {
        info.jsEvent.preventDefault();
        const { entry } = info.event.extendedProps as EventProps;
        opener.current = info.el;
        returnId.current = entry.item.id;
        setSelected(entry);
      },
      datesSet: (info) => {
        const next = {
          start: localDateOf(info.startStr),
          end: localDateOf(info.endStr),
        };
        setRange((current) =>
          current?.start === next.start && current.end === next.end
            ? current
            : next,
        );
      },
    }),
    [t, timeZone, language, context, initial],
  );

  const fetching = schedule.isFetching || query === null;
  let status = '';
  if (schedule.isSuccess && !schedule.isFetching) {
    status =
      entries.length === 0
        ? t('mySchedule.empty')
        : t('mySchedule.count', { count: entries.length });
  } else if (fetching) {
    status = t('mySchedule.loading');
  }

  const go = (date: LocalDate) => onPositionChange({ ...position, date });

  const returnFocus = () => {
    const sessionId = returnId.current ?? '';
    const element = opener.current;
    const target =
      element?.isConnected === true && element.dataset.sessionId === sessionId
        ? element
        : region.current?.querySelector<HTMLElement>(
            `[data-session-id="${sessionId}"]`,
          );
    target?.focus();
  };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div
        role="toolbar"
        aria-label={t('scheduling.calendar.toolbar')}
        className="flex flex-wrap items-center justify-between gap-3"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" onClick={() => go(today)}>
            {t('scheduling.calendar.today')}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t(`scheduling.calendar.previous.${position.view}`)}
            onClick={() => go(shiftDate(position.view, position.date, -1))}
          >
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t(`scheduling.calendar.next.${position.view}`)}
            onClick={() => go(shiftDate(position.view, position.date, 1))}
          >
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
        <div
          role="group"
          aria-label={t('scheduling.calendar.viewsLabel')}
          className="flex flex-wrap gap-1"
        >
          {VIEW_ORDER.map((view) => (
            <Button
              key={view}
              type="button"
              variant={view === position.view ? 'default' : 'outline'}
              aria-pressed={view === position.view}
              onClick={() => onPositionChange({ ...position, view })}
            >
              {t(`scheduling.calendar.views.${view}`)}
            </Button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2
          id={`${id}-title`}
          className="text-xl font-semibold"
          aria-live="polite"
        >
          {controller.view?.title ?? ''}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t('scheduling.calendar.zone', { zone: timeZone })}
        </p>
      </div>

      <p role="status" className="flex min-h-5 items-center gap-2 text-sm">
        {fetching ? <Spinner /> : null}
        {status}
      </p>
      {schedule.data?.truncated ? (
        <p className="rounded-md border border-border bg-muted p-3 text-sm">
          {t('mySchedule.truncated')}
        </p>
      ) : null}
      {schedule.isError
        ? (renderError?.(schedule.error, () => void schedule.refetch()) ?? (
            <ErrorAlert
              error={schedule.error}
              onRetry={() => void schedule.refetch()}
            />
          ))
        : null}

      <div
        ref={region}
        role="region"
        aria-label={t('mySchedule.regionLabel')}
        aria-busy={fetching}
        aria-describedby={`${id}-alt`}
        className="cl-calendar min-w-0 overflow-x-auto"
      >
        <p id={`${id}-alt`} className="sr-only">
          {t('scheduling.calendar.listAlternative')}
        </p>
        <FullCalendar controller={controller} {...options} events={events} />
      </div>

      <EntryDialog
        entry={selected}
        context={context}
        onClose={() => setSelected(null)}
        onReturnFocus={returnFocus}
        action={selected ? renderAction?.(selected) : null}
      />
    </div>
  );
}
