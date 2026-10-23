import FullCalendar, {
  useCalendarController,
  type CalendarOptions,
} from '@fullcalendar/react';
import esLocale from '@fullcalendar/react/locales/es';
import '@fullcalendar/react/skeleton.css';
import '@fullcalendar/react/themes/classic/theme.css';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Plus } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
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
  type ClassSessionUpdate,
} from './api';
import { CalendarFilters } from './CalendarFilters';
import { MoveNotSavedDialog } from './dnd/MoveNotSavedDialog';
import { MoveNoticeView } from './dnd/MoveNotice';
import { RescheduleDialog } from './dnd/RescheduleDialog';
import {
  droppedSlot,
  isEditable,
  overrideBody,
  refusalKind,
} from './dnd/rules';
import { useMoveSession } from './dnd/useMoveSession';
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
// filters).
//
// Rescheduling (#58): a holder of schedule.manage drags an editable event
// (dnd/rules isEditable) in Week and Day (start and duration kept; the end
// resizes; 15-minute snap) or Month (the date; times kept), or uses the
// event dialog's "Reschedule" (keyboard and single pointer). Every move is
// the same PATCH through dnd/useMoveSession: optimistic, reverted on any
// refusal, the "Move not saved" dialog for a conflict (with the override
// for holders of schedule.override). No selecting or date-click.

export interface ScheduleCalendarProps {
  state: CalendarState;
  onStateChange: (state: CalendarState) => void;
  /** The center's IANA zone (useCenterTimeZone). */
  timeZone: string;
  /** Today in the center zone. */
  today: LocalDate;
  loaders: CalendarLoaders;
  /**
   * schedule.manage: the "Schedule a session" link, and moving sessions
   * (drag, resize, Reschedule) when `loaders.updateSession` is given.
   */
  canSchedule: boolean;
  /** schedule.override: "Override and move" after a soft conflict. */
  canOverride?: boolean;
}

interface EventProps {
  item: CalendarItem;
  pending: boolean;
  /** Draggable now (`cl-event--movable`). */
  movable: boolean;
}

interface ChangeInfo {
  event: { start: Date | null; end: Date | null; extendedProps: unknown };
  revert: () => void;
}

/** The local date of a FullCalendar range edge ("2026-11-09…"). */
const LOCALES = [esLocale];

/** Hour labels: 24-hour in Spanish, 12-hour in English (as lib/time). */
const SLOT_FORMATS = {
  es: { hour: 'numeric', minute: '2-digit', hour12: false },
  en: { hour: 'numeric', minute: '2-digit', hour12: true },
} as const;

/** Day and week titles name the dates, not only the month. In Month a
 * drag moves the date only: no resize (#58 D4). */
const VIEW_OPTIONS = {
  timeGridDay: {
    titleFormat: { year: 'numeric', month: 'long', day: 'numeric' },
  },
  timeGridWeek: {
    titleFormat: { year: 'numeric', month: 'short', day: 'numeric' },
  },
  dayGridMonth: { eventDurationEditable: false },
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
  canOverride = false,
}: ScheduleCalendarProps) {
  const { t } = useTranslation();
  const { language } = useLanguage();
  const id = useId();
  const controller = useCalendarController();
  const [range, setRange] = useState<LocalRange | null>(null);
  const [selected, setSelected] = useState<CalendarItem | null>(null);
  const [rescheduling, setRescheduling] = useState<CalendarItem | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const opener = useRef<HTMLElement | null>(null);
  // Focus after a dialog closes (see focusEvent below).
  const focusNotice = useRef(false);
  const skipReturn = useRef(false);
  const returnId = useRef<string | null>(null);
  // FullCalendar's first view and date; later changes go through the
  // controller (the effect below).
  const [initial] = useState(() => ({ view: state.view, date: state.date }));
  const region = useRef<HTMLDivElement | null>(null);
  const noticeRef = useRef<HTMLDivElement | null>(null);
  const canMove = canSchedule && loaders.updateSession !== undefined;
  const move = useMoveSession(canMove ? loaders.updateSession : undefined);

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
  const loaded = useMemo(() => sessions.data?.items ?? [], [sessions.data]);
  const { positions } = move;
  // A move being saved shows at its new slot (#58 D6).
  const items = useMemo(
    () =>
      loaded.map((item) =>
        positions[item.id] ? { ...item, ...positions[item.id] } : item,
      ),
    [loaded, positions],
  );
  /** The loaded item now (a dialog opened on an older copy). */
  const current = (item: CalendarItem | null) =>
    item ? (loaded.find((found) => found.id === item.id) ?? item) : null;

  // Names for the filter chips after a reload: from the loaded sessions.
  const knownNames = useMemo(() => {
    const found: Record<string, string> = {};
    for (const item of items) {
      if (item.tutor) found[item.tutor.id] = item.tutor.display_name;
      for (const p of item.participants) found[p.student_id] = p.display_name;
    }
    return { ...found, ...names };
  }, [items, names]);

  // "Not started" is judged at the last load (pure); the server stays the
  // authority (a stale one is 409 session_not_editable).
  const loadedAt = sessions.dataUpdatedAt;
  const events = useMemo(() => {
    return items.map((item) => {
      const pending = item.id in positions;
      const movable = isEditable(item, loadedAt, canMove) && !pending;
      return {
        id: item.id,
        start: item.starts_at,
        end: item.ends_at,
        title: item.subject.name,
        // Editable events follow the calendar's (and the view's) options;
        // the others, and one being saved, are fixed (#58 D3, D6).
        ...(movable ? {} : { editable: false }),
        extendedProps: { item, pending, movable } satisfies EventProps,
      };
    });
  }, [items, positions, canMove, loadedAt]);

  // A drop or a resize (stable: `move.drop` is).
  const { drop } = move;
  const onChange = useCallback(
    (info: ChangeInfo) => {
      const { item } = info.event.extendedProps as EventProps;
      const slot = droppedSlot(info.event.start, info.event.end);
      if (!slot) {
        info.revert();
        return;
      }
      void drop(item, slot, info.revert);
    },
    [drop],
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
      // #58 D4: drag (start) and end-resize for schedule.manage, 15 minutes.
      eventStartEditable: canMove,
      eventDurationEditable: canMove,
      eventResizableFromStart: false,
      snapDuration: '00:15:00',
      eventDrop: onChange,
      eventResize: onChange,
      eventClass: (info) => {
        const { item, pending, movable } = info.event
          .extendedProps as EventProps;
        return [
          'cl-event',
          `cl-event--${item.state}`,
          pending ? 'cl-event--pending' : '',
          movable ? 'cl-event--movable' : '',
        ]
          .filter(Boolean)
          .join(' ');
      },
      eventContent: (info) => {
        const { item, pending } = info.event.extendedProps as EventProps;
        return (
          <EventContent
            item={item}
            context={context}
            compact={isCompact(info.view.type, item)}
            pending={pending}
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
        const { item } = info.event.extendedProps as EventProps;
        opener.current = info.el;
        returnId.current = item.id;
        setSelected(item);
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
    [t, timeZone, language, context, initial, canMove, onChange],
  );
  const refused = move.refused;
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

  // Where the focus goes when a dialog closes: the event it is about
  // (`returnId`, found again by its id: a move re-renders it), or the page
  // notice after a refusal the dialog didn't show (`focusNotice`), or
  // nowhere when the details make way for Reschedule (`skipReturn`).
  useEffect(() => {
    if (refused) {
      returnId.current = refused.item.id;
    }
  }, [refused]);
  const focusEvent = () => {
    const id = returnId.current ?? '';
    const element = opener.current;
    const target =
      element?.isConnected === true && element.dataset.sessionId === id
        ? element
        : region.current?.querySelector<HTMLElement>(
            `[data-session-id="${id}"]`,
          );
    target?.focus();
  };
  const afterDialog = (event: Event) => {
    event.preventDefault();
    if (focusNotice.current) {
      focusNotice.current = false;
      return;
    }
    focusEvent();
  };
  const refuseFromDialog = (
    item: CalendarItem,
    body: ClassSessionUpdate,
    error: unknown,
  ) => {
    focusNotice.current = true;
    move.refuse(item, body, error, { focus: true });
  };

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

      {canMove ? (
        <MoveNoticeView
          notice={move.notice}
          context={context}
          onReload={() => void move.reload()}
          noticeRef={noticeRef}
        />
      ) : null}

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
        item={current(selected)}
        context={context}
        fetchSeriesId={loaders.fetchSeriesId}
        canReschedule={(item) => isEditable(item, loadedAt, canMove)}
        onReschedule={(item) => {
          // One dialog at a time: Reschedule replaces the details.
          skipReturn.current = true;
          setSelected(null);
          setRescheduling(item);
        }}
        onClose={() => setSelected(null)}
        onReturnFocus={() => {
          if (skipReturn.current) {
            skipReturn.current = false;
            return;
          }
          focusEvent();
        }}
      />

      {canMove ? (
        <>
          <RescheduleDialog
            item={current(rescheduling)}
            context={context}
            canOverride={canOverride}
            send={async (item, body) => {
              await move.send(item, body);
              setRescheduling(null);
            }}
            onRefused={(item, body, error) => {
              setRescheduling(null);
              refuseFromDialog(item, body, error);
            }}
            onCancel={() => setRescheduling(null)}
            onCloseAutoFocus={afterDialog}
          />
          <MoveNotSavedDialog
            refused={refused}
            context={context}
            canOverride={canOverride}
            onConflicts={(conflicts) =>
              move.setRefused((now) => (now ? { ...now, conflicts } : now))
            }
            onOverride={async (reason) => {
              if (!refused) {
                return;
              }
              // The same body and version as the refused move (#58 F3).
              const body = overrideBody(
                refused.body,
                refused.conflicts,
                reason,
              );
              try {
                await move.send(refused.item, body);
                move.setRefused(null);
              } catch (error) {
                const kind = refusalKind(error);
                if (kind === 'stale' || kind === 'gone') {
                  move.setRefused(null);
                  refuseFromDialog(refused.item, body, error);
                  return;
                }
                throw error;
              }
            }}
            onClose={() => move.setRefused(null)}
            onCloseAutoFocus={afterDialog}
          />
        </>
      ) : null}
    </div>
  );
}
