import {
  Ban,
  CalendarOff,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleHelp,
  TriangleAlert,
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
// visible parts are hidden from them so nothing is read twice.

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

export function MarkerLabel({ code }: { code: MarkerCode }) {
  const { t } = useTranslation();
  const Icon = MARKER_ICONS[code];
  return (
    <span className="cl-badge cl-badge--marker">
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {t(`scheduling.markers.${code}.label`)}
    </span>
  );
}

export function EventContent({
  item,
  context,
}: {
  item: CalendarItem;
  context: LabelContext;
}) {
  return (
    <span className="cl-event-content">
      <span className="sr-only">{sessionName(item, context)}</span>
      <span aria-hidden="true" className="cl-event-body">
        <span className="cl-event-line">
          <span className="cl-event-time">
            {sessionTimeText(item, context)}
          </span>{' '}
          <StateLabel state={item.state} />
        </span>
        <span className="cl-event-title">{item.subject.name}</span>
        {item.markers.length > 0 ? (
          <span className="cl-event-badges">
            {item.markers.map((code) => (
              <MarkerLabel key={code} code={code} />
            ))}
          </span>
        ) : null}
        <span className="cl-event-line">{studentNames(item, context)}</span>
      </span>
    </span>
  );
}
