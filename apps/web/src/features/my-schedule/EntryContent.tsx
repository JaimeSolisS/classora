import { STATE_ICONS } from '@/features/calendar/EventContent';
import { sessionTimeText } from '@/features/calendar/labels';
import type { ScheduleEntry } from './api';
import {
  entryName,
  roomText,
  stateLabel,
  whoText,
  type LabelContext,
} from './labels';

// One event of "My schedule" (#67 E5, E6): the state as an icon with its
// text (and the #57 border style, calendar.css), the time range, the
// subject, who (the tutor for a family, the students for a tutor) and the
// room. Screen readers get one name with every detail (entryName); the
// visible parts are hidden from them so nothing is read twice. No markers.

export function EntryStateLabel({
  entry,
  context,
}: {
  entry: ScheduleEntry;
  context: LabelContext;
}) {
  const Icon = STATE_ICONS[entry.item.state];
  return (
    <span className="cl-badge">
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {stateLabel(entry, context.t)}
    </span>
  );
}

export function EntryContent({
  entry,
  context,
  compact = false,
}: {
  entry: ScheduleEntry;
  context: LabelContext;
  /** Time-grid events: the state first, then the time. */
  compact?: boolean;
}) {
  const room = roomText(entry, context.t);
  return (
    <span className="cl-event-content">
      <span className="sr-only">{entryName(entry, context)}</span>
      <span aria-hidden="true" className="cl-event-body">
        {compact ? (
          <>
            <span className="cl-event-status">
              <EntryStateLabel entry={entry} context={context} />
            </span>
            <span className="cl-event-time">
              {sessionTimeText(entry.item, context)}
            </span>
          </>
        ) : (
          <span className="cl-event-line">
            <span className="cl-event-time">
              {sessionTimeText(entry.item, context)}
            </span>{' '}
            <EntryStateLabel entry={entry} context={context} />
          </span>
        )}
        <span className="cl-event-title">{entry.item.subject.name}</span>
        <span className="cl-event-line">{whoText(entry, context.t)}</span>
        {room ? <span className="cl-event-line">{room}</span> : null}
      </span>
    </span>
  );
}
