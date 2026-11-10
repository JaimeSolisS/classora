import type { TFunction } from 'i18next';
import type { Language } from '@/i18n/language';
import {
  formatInstant,
  formatUtcOffset,
  hasOffsetChange,
  toLocalDate,
  type Instant,
} from '@/lib/time';
import type { CalendarItem } from './api';

// The texts of a calendar event (#57 D7): its time range in the center zone
// and its accessible name. Pure: the caller passes `t`, the zone and the
// language.

export interface LabelContext {
  t: TFunction<'common'>;
  timeZone: string;
  language: Language;
}

function time(instant: Instant, { timeZone, language }: LabelContext): string {
  const text = formatInstant(instant, { timeZone, language, style: 'time' });
  // On a DST transition day a wall-clock time can happen twice (fall back):
  // the offset tells the two apart.
  return hasOffsetChange(toLocalDate(instant, timeZone), timeZone)
    ? `${text} (${formatUtcOffset(instant, timeZone)})`
    : text;
}

/** "10:00–11:00" in the center zone, with the offsets on a DST day. */
export function sessionTimeText(
  item: Pick<CalendarItem, 'starts_at' | 'ends_at'>,
  context: LabelContext,
): string {
  return `${time(item.starts_at, context)}–${time(item.ends_at, context)}`;
}

/** The students' names, or "No students". */
export function studentNames(item: CalendarItem, { t }: LabelContext): string {
  return item.participants.length > 0
    ? item.participants.map((p) => p.display_name).join(', ')
    : t('scheduling.calendar.noStudents');
}

/** HF-07 E3: "Tutor: Carmen Ruiz", or "No tutor". The session's current
 * tutor (the substitute after a cover). */
export function tutorText(
  item: Pick<CalendarItem, 'tutor'>,
  { t }: Pick<LabelContext, 't'>,
): string {
  return item.tutor
    ? t('scheduling.calendar.tutorLine', { name: item.tutor.display_name })
    : t('scheduling.calendar.noTutor');
}

/** The class type as text: "Individual", "Group" or "Course". */
export function kindText(
  item: Pick<CalendarItem, 'kind'>,
  { t }: Pick<LabelContext, 't'>,
): string {
  return t(`scheduling.calendar.filters.kinds.${item.kind}`);
}

/**
 * The event's accessible name: subject, class type, students, time range,
 * tutor (or "No tutor"), state, room (if any) and markers, e.g.
 * "Mathematics, Individual, Ana López, 10:00–11:00, No tutor, Unassigned,
 * Room 2, Center closed".
 */
export function sessionName(item: CalendarItem, context: LabelContext): string {
  const { t } = context;
  return [
    item.subject.name,
    kindText(item, context),
    studentNames(item, context),
    sessionTimeText(item, context),
    item.tutor?.display_name ?? t('scheduling.calendar.noTutor'),
    t(`scheduling.states.${item.state}`),
    item.classroom?.name,
    ...item.markers.map((code) => t(`scheduling.markers.${code}.label`)),
  ]
    .filter((part): part is string => Boolean(part))
    .join(', ');
}

/** Shorter time-grid events than this use the compact layout. */
export const COMPACT_MINUTES = 90;

/**
 * Whether an event is drawn compactly: a time-grid (day or week) event of
 * less than COMPACT_MINUTES. Its first line then holds the state (icon and
 * text) and the markers as icons, so even a 15-minute event shows them.
 */
export function isCompact(viewType: string, item: CalendarItem): boolean {
  const minutes =
    (Date.parse(item.ends_at) - Date.parse(item.starts_at)) / 60_000;
  return viewType.startsWith('timeGrid') && minutes < COMPACT_MINUTES;
}
