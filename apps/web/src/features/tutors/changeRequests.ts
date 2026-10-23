import type { TFunction } from 'i18next';
import type { InstantStyle } from '@/lib/time';

// Display text of an availability change request (M4-18 #63 H1, H2): the
// tutor's availability section and the approval inbox describe the change
// and its affected sessions the same way. Pure: the caller passes `t` and
// the formatters (center zone, UI language).

/** The fields of a change that the texts read (the request item and the
 * inbox summary have them). */
export interface ChangeLike {
  kind: 'weekly' | 'exception_create' | 'exception_delete';
  change: {
    windows?:
      | readonly {
          weekday: number;
          starts_at: string;
          ends_at: string;
        }[]
      | null;
    exception?: {
      kind: 'unavailable' | 'available';
      starts_on: string;
      ends_on: string;
      starts_at: string | null;
      ends_at: string | null;
    } | null;
  };
}

export interface AffectedSessionLike {
  id: string;
  starts_at: string;
  ends_at: string;
  subject_name: string;
}

type InstantFormatter = (instant: string, style?: InstantStyle) => string;
type DateFormatter = (localDate: string) => string;

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;

function weeklyText(
  windows: NonNullable<ChangeLike['change']['windows']>,
  t: TFunction,
): string {
  const days = WEEKDAYS.map((weekday) => {
    const ranges = windows
      .filter((window) => window.weekday === weekday)
      .map((window) =>
        t('weeklyWindows.range', {
          start: window.starts_at,
          end: window.ends_at,
        }),
      );
    return ranges.length > 0
      ? `${t(`weeklyWindows.weekdays.${weekday}`)} ${ranges.join(', ')}`
      : null;
  }).filter((day): day is string => day !== null);
  return days.length > 0
    ? days.join('; ')
    : t('tutors.availability.changes.noWindows');
}

/** What the change does, e.g. "Weekly availability: Monday 08:00–16:00",
 * "Time off: 12 Nov 2031 – 16 Nov 2031" or "Remove extra availability:
 * 7 Jan 2031, 10:00–12:00". */
export function describeChange(
  request: ChangeLike,
  t: TFunction,
  formatDate: DateFormatter,
): string {
  const { windows, exception } = request.change;
  if (request.kind === 'weekly') {
    return t('tutors.availability.changes.weekly', {
      windows: weeklyText(windows ?? [], t),
    });
  }
  if (!exception) {
    return t(`tutors.availability.changes.kinds.${request.kind}`);
  }
  const dates =
    exception.starts_on === exception.ends_on
      ? formatDate(exception.starts_on)
      : t('tutors.availability.dateRange', {
          start: formatDate(exception.starts_on),
          end: formatDate(exception.ends_on),
        });
  const when =
    exception.starts_at && exception.ends_at
      ? t('tutors.availability.changes.datesHours', {
          dates,
          hours: t('weeklyWindows.range', {
            start: exception.starts_at,
            end: exception.ends_at,
          }),
        })
      : dates;
  return t(
    request.kind === 'exception_create'
      ? `tutors.availability.changes.add.${exception.kind}`
      : `tutors.availability.changes.remove.${exception.kind}`,
    { when },
  );
}

/** One affected session: "6 Jan 2031, 16:00–17:00 · Physics" (center
 * zone). */
export function affectedSessionText(
  session: AffectedSessionLike,
  t: TFunction,
  formatInstant: InstantFormatter,
): string {
  return t('tutors.availability.changes.session', {
    when: formatInstant(session.starts_at, 'dateTime'),
    end: formatInstant(session.ends_at, 'time'),
    subject: session.subject_name,
  });
}

/** How many sessions a list leaves out ("and N more"), or 0. */
export function moreCount(total: number, shown: number): number {
  return Math.max(0, total - shown);
}
