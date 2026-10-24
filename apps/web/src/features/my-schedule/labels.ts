import type { TFunction } from 'i18next';
import { sessionTimeText, type LabelContext } from '@/features/calendar/labels';
import { formatLocalDate, toLocalDate } from '@/lib/time';
import type { ScheduleEntry } from './api';

// The texts of one "My schedule" item (#67 E5, E6): the state label, who
// (the tutor for a family, the students for a tutor) and the accessible
// name. Pure: the caller passes `t`, the zone and the language.

export type { LabelContext };

/** The state as text: the family and tutor wording (#67 E6). */
export function stateLabel(
  entry: ScheduleEntry,
  t: TFunction<'common'>,
): string {
  return t(`mySchedule.states.${entry.item.state}`);
}

/** The tutor (family) or the students (tutor). */
export function whoText(entry: ScheduleEntry, t: TFunction<'common'>): string {
  if (entry.audience === 'family') {
    return entry.item.tutor
      ? t('mySchedule.tutor', { name: entry.item.tutor.display_name })
      : t('mySchedule.noTutor');
  }
  return entry.item.participants.length > 0
    ? entry.item.participants.map((p) => p.display_name).join(', ')
    : t('mySchedule.noStudents');
}

export function roomText(
  entry: ScheduleEntry,
  t: TFunction<'common'>,
): string | undefined {
  return entry.item.classroom
    ? t('mySchedule.room', { name: entry.item.classroom.name })
    : undefined;
}

export function dateText(entry: ScheduleEntry, context: LabelContext): string {
  return formatLocalDate(toLocalDate(entry.item.starts_at, context.timeZone), {
    language: context.language,
    style: 'long',
  });
}

/**
 * The item's accessible name: subject, date, time range, state, who and the
 * room (if any), e.g. "Mathematics, November 9, 2026, 10:00–11:00,
 * Confirmed, Tutor: Carmen Ruiz, Room: Aula 2". No markers (#67 E6).
 */
export function entryName(entry: ScheduleEntry, context: LabelContext): string {
  const { t } = context;
  return [
    entry.item.subject.name,
    dateText(entry, context),
    sessionTimeText(entry.item, context),
    stateLabel(entry, t),
    whoText(entry, t),
    roomText(entry, t),
  ]
    .filter((part): part is string => Boolean(part))
    .join(', ');
}
