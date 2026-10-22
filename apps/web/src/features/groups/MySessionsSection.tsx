import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { STATE_ICONS } from '@/features/calendar/EventContent';
import { useFormatters } from '@/lib/formatters';
import { addDays, toLocalDate } from '@/lib/time';
import type { GroupSessionItem } from './api';
import { WINDOW_DAYS } from './rules';

// "My sessions" (#234 C4), the student view: the group's sessions in a
// 4-week window that starts on a local date (today by default), with
// "Previous 4 weeks" / "Next 4 weeks". The API returns only the confirmed
// or completed sessions the student takes part in (#56 H3). Each item: the
// local date and time in the center zone, the state as text and an icon,
// the tutor and the room. Presentational: the page passes the window and
// the query state.

export type MySessionsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly GroupSessionItem[] };

export interface MySessionsSectionProps {
  /** The window's first local date (YYYY-MM-DD). */
  windowStart: string;
  state: MySessionsState;
  timeZone: string;
  onPrevious: () => void;
  onNext: () => void;
}

export function MySessionsSection({
  windowStart,
  state,
  timeZone,
  onPrevious,
  onNext,
}: MySessionsSectionProps) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const id = useId();
  const last = addDays(windowStart, WINDOW_DAYS - 1);

  let content;
  if (state.status === 'loading') {
    content = <LoadingState rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    content = <p className="text-sm">{t('groups.mySessions.none')}</p>;
  } else {
    content = (
      <ol aria-labelledby={`${id}-heading`} className="flex flex-col gap-2">
        {state.items.map((item) => {
          const Icon = STATE_ICONS[item.state];
          return (
            <li
              key={item.id}
              className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm"
            >
              <p className="font-medium break-words">
                {formatLocalDate(toLocalDate(item.starts_at, timeZone), 'long')}{' '}
                ·{' '}
                {t('groups.timeRange', {
                  start: formatInstant(item.starts_at, 'time'),
                  end: formatInstant(item.ends_at, 'time'),
                })}
              </p>
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="inline-flex items-center gap-1">
                  <Icon aria-hidden="true" className="size-3.5 shrink-0" />
                  {t(`scheduling.states.${item.state}`)}
                </span>
                <span className="break-words">
                  {t('groups.mySessions.tutor', {
                    name: item.tutor?.display_name ?? t('groups.noTutor'),
                  })}
                </span>
                <span className="break-words">
                  {t('groups.mySessions.classroom', {
                    name: item.classroom?.name ?? t('scheduling.noClassroom'),
                  })}
                </span>
              </p>
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <section aria-labelledby={`${id}-heading`} className="flex flex-col gap-3">
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('groups.mySessions.heading')}
      </h2>
      <p className="text-sm text-muted-foreground">
        {t('groups.mySessions.window', {
          from: formatLocalDate(windowStart),
          to: formatLocalDate(last),
          zone: timeZone,
        })}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onPrevious}>
          {t('groups.mySessions.previous')}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onNext}>
          {t('groups.mySessions.next')}
        </Button>
      </div>
      {content}
    </section>
  );
}
