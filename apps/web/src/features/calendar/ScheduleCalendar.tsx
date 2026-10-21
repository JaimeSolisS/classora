import FullCalendar, {
  useCalendarController,
  type CalendarOptions,
} from '@fullcalendar/react';
import esLocale from '@fullcalendar/react/locales/es';
import '@fullcalendar/react/skeleton.css';
import '@fullcalendar/react/themes/classic/theme.css';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Plus } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { useLanguage } from '@/i18n/use-language';
import { parseLocalDate, type LocalDate } from '@/lib/time';
import {
  calendarQueryKey,
  type CalendarItem,
  type CalendarLoaders,
  type CalendarQuery,
} from './api';
import { CalendarFilters } from './CalendarFilters';
import { EventContent } from './EventContent';
import { isCompact, type LabelContext } from './labels';
import { SessionDialog } from './SessionDialog';
import {
  filterQuery,
  rangeInstants,
  shiftDate,
  type CalendarState,
  type LocalRange,
} from './urlState';
import { CALENDAR_PLUGINS, CALENDAR_VIEWS, VIEW_ORDER } from './views';
import './calendar.css';

// The calendar (#57 D): FullCalendar v7 (ADR-0005 D8) in the center time
// zone, never the browser's, with Classora's own toolbar and filters. The
// state (view, date, filters) comes from the page (the URL); the visible
// range comes from FullCalendar (`datesSet`) and is read as local dates,
// turned into `from`/`to` instants by lib/time. One query per (range,
// filters). Read-only: no dragging, selecting or date-click (#58).

export interface ScheduleCalendarProps {
  state: CalendarState;
  onStateChange: (state: CalendarState) => void;
  /** The center's IANA zone (useCenterTimeZone). */
  timeZone: string;
  /** Today in the center zone. */
  today: LocalDate;
  loaders: CalendarLoaders;
  /** Shows the "Schedule a session" link (schedule.manage). */
  canSchedule: boolean;
}

interface EventProps {
  item: CalendarItem;
}

/** The local date of a FullCalendar range edge ("2026-11-09…"). */
const LOCALES = [esLocale];

/** Hour labels: 24-hour in Spanish, 12-hour in English (as lib/time). */
const SLOT_FORMATS = {
  es: { hour: 'numeric', minute: '2-digit', hour12: false },
  en: { hour: 'numeric', minute: '2-digit', hour12: true },
} as const;

/** Day and week titles name the dates, not only the month. */
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

export function ScheduleCalendar({
  state,
  onStateChange,
  timeZone,
  today,
  loaders,
  canSchedule,
}: ScheduleCalendarProps) {
  const { t } = useTranslation();
  const { language } = useLanguage();
  const id = useId();
  const controller = useCalendarController();
  const [range, setRange] = useState<LocalRange | null>(null);
  const [selected, setSelected] = useState<CalendarItem | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const opener = useRef<HTMLElement | null>(null);
  // FullCalendar's first view and date; later changes go through the
  // controller (the effect below).
  const [initial] = useState(() => ({ view: state.view, date: state.date }));
  const region = useRef<HTMLDivElement | null>(null);

  // Keep FullCalendar on the URL's view and date (back/forward, the toolbar).
  useEffect(() => {
    if (!controller.view) {
      return;
    }
    if (controller.view.type !== CALENDAR_VIEWS[state.view]) {
      controller.changeView(CALENDAR_VIEWS[state.view]);
    }
    controller.gotoDate(state.date);
  }, [controller, state.view, state.date]);

  const query: CalendarQuery | null = range
    ? { ...rangeInstants(range, timeZone), ...filterQuery(state.filters) }
    : null;
  const sessions = useQuery({
    queryKey: calendarQueryKey(query ?? { from: '', to: '' }),
    queryFn: ({ signal }) => loaders.fetchCalendar(query!, signal),
    enabled: query !== null,
  });
  const items = useMemo(() => sessions.data?.items ?? [], [sessions.data]);

  // Names for the filter chips after a reload: from the loaded sessions.
  const knownNames = useMemo(() => {
    const found: Record<string, string> = {};
    for (const item of items) {
      if (item.tutor) found[item.tutor.id] = item.tutor.display_name;
      for (const p of item.participants) found[p.student_id] = p.display_name;
    }
    return { ...found, ...names };
  }, [items, names]);

  const events = useMemo(
    () =>
      items.map((item) => ({
        id: item.id,
        start: item.starts_at,
        end: item.ends_at,
        title: item.subject.name,
        extendedProps: { item } satisfies EventProps,
      })),
    [items],
  );

  const context: LabelContext = useMemo(
    () => ({ t, timeZone, language }),
    [t, timeZone, language],
  );

  // Stable options: FullCalendar recomputes its range (and calls datesSet)
  // when an option changes identity, so nothing here is rebuilt per render.
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
      noEventsText: t('scheduling.calendar.empty'),
      eventInteractive: true,
      eventClass: (info) =>
        `cl-event cl-event--${(info.event.extendedProps as EventProps).item.state}`,
      eventContent: (info) => {
        const { item } = info.event.extendedProps as EventProps;
        return (
          <EventContent
            item={item}
            context={context}
            compact={isCompact(info.view.type, item)}
          />
        );
      },
      // A 15-minute event keeps one full line: its state and markers.
      eventMinHeight: 22,
      eventDidMount: (info) => {
        info.el.dataset.sessionId = info.event.id;
      },
      eventClick: (info) => {
        info.jsEvent.preventDefault();
        opener.current = info.el;
        setSelected((info.event.extendedProps as EventProps).item);
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
  const fetching = sessions.isFetching || query === null;

  let status = '';
  if (sessions.isSuccess && !sessions.isFetching) {
    status =
      items.length === 0
        ? t('scheduling.calendar.empty')
        : t('scheduling.calendar.count', { count: items.length });
  } else if (fetching) {
    status = t('scheduling.calendar.loading');
  }

  const go = (date: LocalDate) => onStateChange({ ...state, date });

  return (
    <div className="flex flex-col gap-4">
      <CalendarFilters
        filters={state.filters}
        onChange={(filters) => onStateChange({ ...state, filters })}
        loaders={loaders}
        names={knownNames}
        onName={(key, name) => setNames((known) => ({ ...known, [key]: name }))}
      />

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
            aria-label={t(`scheduling.calendar.previous.${state.view}`)}
            onClick={() => go(shiftDate(state.view, state.date, -1))}
          >
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={t(`scheduling.calendar.next.${state.view}`)}
            onClick={() => go(shiftDate(state.view, state.date, 1))}
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
              variant={view === state.view ? 'default' : 'outline'}
              aria-pressed={view === state.view}
              onClick={() => onStateChange({ ...state, view })}
            >
              {t(`scheduling.calendar.views.${view}`)}
            </Button>
          ))}
        </div>
        {canSchedule ? (
          <Link to="/schedule/new" className={LINK_BUTTON_CLASS}>
            <Plus aria-hidden="true" className="mr-1.5 size-4" />
            {t('scheduling.calendar.scheduleSession')}
          </Link>
        ) : null}
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
      {sessions.data?.truncated ? (
        <p className="rounded-md border border-border bg-muted p-3 text-sm">
          {t('scheduling.calendar.truncated')}
        </p>
      ) : null}
      {sessions.isError ? (
        <ErrorAlert
          error={sessions.error}
          onRetry={() => void sessions.refetch()}
        />
      ) : null}

      <div
        ref={region}
        role="region"
        aria-label={t('scheduling.calendar.regionLabel')}
        aria-busy={fetching}
        aria-describedby={`${id}-alt`}
        className="cl-calendar"
      >
        <p id={`${id}-alt`} className="sr-only">
          {t('scheduling.calendar.listAlternative')}
        </p>
        <FullCalendar controller={controller} {...options} events={events} />
      </div>

      <SessionDialog
        item={selected}
        context={context}
        onClose={() => setSelected(null)}
        onReturnFocus={() => {
          const element = opener.current;
          const target =
            element?.isConnected === true
              ? element
              : region.current?.querySelector<HTMLElement>(
                  `[data-session-id="${selected?.id ?? ''}"]`,
                );
          target?.focus();
        }}
      />
    </div>
  );
}
