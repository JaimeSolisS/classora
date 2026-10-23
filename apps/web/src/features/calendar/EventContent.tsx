import {
  Ban,
  CalendarOff,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleHelp,
  LoaderCircle,
  ShieldAlert,
  TriangleAlert,
  UserMinus,
  UserX,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { CalendarItem, MarkerCode, SessionState } from './api';
import {
  sessionName,
  sessionTimeText,
  studentNames,
  type LabelContext,
} from './labels';

// One event on the calendar (#57 D7). The state shows as a border style
// (calendar.css) and as an icon with its text; markers as an icon with
// text. Screen readers get one name with every detail (sessionName); the
// visible parts are hidden from them so nothing is read twice. A move being
// saved (#58 D6) shows "Saving…" (icon and text) and is part of the name.

export const STATE_ICONS: Record<SessionState, LucideIcon> = {
  unassigned: CircleDashed,
  offered: CircleHelp,
  confirmed: CircleCheck,
  completed: CircleDot,
  cancelled: Ban,
  skipped_closure: CalendarOff,
  conflict: TriangleAlert,
};

export const MARKER_ICONS: Record<MarkerCode, LucideIcon> = {
  center_closed: CalendarOff,
  tutor_not_qualified: UserX,
  overridden: ShieldAlert,
  reassignment_needed: UserMinus,
};

export function StateLabel({ state }: { state: SessionState }) {
  const { t } = useTranslation();
  const Icon = STATE_ICONS[state];
  return (
    <span className="cl-badge">
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {t(`scheduling.states.${state}`)}
    </span>
  );
}

/** A marker as its icon only (short events); the text is its tooltip and
 * is part of the event's accessible name. */
export function MarkerIcon({ code }: { code: MarkerCode }) {
  const { t } = useTranslation();
  const Icon = MARKER_ICONS[code];
  return (
    <span
      className="cl-badge cl-badge--marker"
      title={t(`scheduling.markers.${code}.label`)}
      data-marker={code}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
    </span>
  );
}

export function MarkerLabel({ code }: { code: MarkerCode }) {
  const { t } = useTranslation();
  const Icon = MARKER_ICONS[code];
  return (
    <span className="cl-badge cl-badge--marker" data-marker={code}>
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {t(`scheduling.markers.${code}.label`)}
    </span>
  );
}

/** "Saving…" on an event whose move is pending (#58 D6). */
export function SavingLabel() {
  const { t } = useTranslation();
  return (
    <span className="cl-badge cl-badge--pending" data-pending="true">
      <LoaderCircle aria-hidden="true" className="size-3.5 shrink-0" />
      {t('scheduling.calendar.move.saving')}
    </span>
  );
}

export function EventContent({
  item,
  context,
  compact = false,
  pending = false,
}: {
  item: CalendarItem;
  context: LabelContext;
  compact?: boolean;
  /** A move of this session is being saved. */
  pending?: boolean;
}) {
  const name = pending
    ? `${sessionName(item, context)}, ${context.t('scheduling.calendar.move.saving')}`
    : sessionName(item, context);
  if (compact) {
    return (
      <span className="cl-event-content">
        <span className="sr-only">{name}</span>
        <span aria-hidden="true" className="cl-event-body">
          <span className="cl-event-status">
            {pending ? <SavingLabel /> : null}
            <StateLabel state={item.state} />
            {item.markers.map((code) => (
              <MarkerIcon key={code} code={code} />
            ))}
          </span>
          <span className="cl-event-time">
            {sessionTimeText(item, context)}
          </span>
          <span className="cl-event-title">{item.subject.name}</span>
          <span className="cl-event-line">{studentNames(item, context)}</span>
        </span>
      </span>
    );
  }
  return (
    <span className="cl-event-content">
      <span className="sr-only">{name}</span>
      <span aria-hidden="true" className="cl-event-body">
        <span className="cl-event-line">
          <span className="cl-event-time">
            {sessionTimeText(item, context)}
          </span>{' '}
          <StateLabel state={item.state} />
          {pending ? (
            <>
              {' '}
              <SavingLabel />
            </>
          ) : null}
        </span>
        {item.markers.length > 0 ? (
          <span className="cl-event-badges">
            {item.markers.map((code) => (
              <MarkerLabel key={code} code={code} />
            ))}
          </span>
        ) : null}
        <span className="cl-event-title">{item.subject.name}</span>
        <span className="cl-event-line">{studentNames(item, context)}</span>
      </span>
    </span>
  );
}
