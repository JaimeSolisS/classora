import { formatLocalDate, toLocalDate } from '@/lib/time';
import { sessionTimeText, type LabelContext } from '../labels';
import type { MoveNotice } from './useMoveSession';

// The announcement of a saved move (#58 D6, F3): "Moved to <date>,
// <times>." in the center zone and the language's format, then "Moved with
// an override." and, for a series occurrence, that it is now an exception
// (#58 E3).

export function moveAnnouncement(
  notice: Extract<MoveNotice, { kind: 'moved' }>,
  context: LabelContext,
): string {
  const { t, timeZone, language } = context;
  const { session } = notice;
  const parts = [
    t('scheduling.calendar.move.moved', {
      date: formatLocalDate(toLocalDate(session.starts_at, timeZone), {
        language,
        style: 'long',
      }),
      time: sessionTimeText(session, context),
    }),
  ];
  if (notice.overridden) {
    parts.push(t('scheduling.calendar.move.movedWithOverride'));
  }
  if (notice.occurrence) {
    parts.push(t('scheduling.calendar.move.occurrence'));
  }
  return parts.join(' ');
}
