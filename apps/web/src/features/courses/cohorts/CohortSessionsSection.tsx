import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { STATE_ICONS } from '@/features/calendar/EventContent';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { addDays, toLocalDate } from '@/lib/time';
import {
  myScheduleHref,
  namesText,
  sessionStateText,
  showsStudentNames,
} from '@/features/groups/nonStaff';
import type { CohortSessionItem } from './api';
import { WINDOW_DAYS } from './rules';

// "Sessions" on the cohort page (#243 D3): the cohort's sessions in a
// 4-week window that starts on a local date (today by default), with
// "Previous 4 weeks" / "Next 4 weeks" (one GET /cohorts/{id}/sessions of
// at most 45 days each). Every row: the local date (a link to the session
// page for schedule.view holders) and time in the center zone, the state
// as text and an icon, "Exception" (text) for a session moved or edited on
// its own, the tutor and the room. Presentational: the page passes the
// window and the query state. The shared Table scrolls when it doesn't fit.
//
// For a tutor, student or guardian (#246 B3, B4; `myScheduleLinks`) the date
// links to that day in "My schedule" (List view), the state uses the "My
// schedule" wording and, for a guardian with several children in the
// list, a "Student" column names whose session it is.

export type CohortSessionsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly CohortSessionItem[] };

export interface CohortSessionsSectionProps {
  /** The window's first local date (YYYY-MM-DD). */
  windowStart: string;
  state: CohortSessionsState;
  timeZone: string;
  /** Link each date to its session page (schedule.view). */
  linkSessions: boolean;
  /** Link each date to "My schedule" instead (non-staff, #246 B4). */
  myScheduleLinks?: boolean;
  onPrevious: () => void;
  onNext: () => void;
}

export function CohortSessionsSection({
  windowStart,
  state,
  timeZone,
  linkSessions,
  myScheduleLinks = false,
  onPrevious,
  onNext,
}: CohortSessionsSectionProps) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const id = useId();
  const headingId = `${id}-heading`;
  const rangeId = `${id}-range`;
  const last = addDays(windowStart, WINDOW_DAYS - 1);

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    content = <p className="text-sm">{t('courses.cohorts.sessions.empty')}</p>;
  } else {
    const named = showsStudentNames(state.items);
    content = (
      // The heading and the window name the table (and its scroll region at
      // 320 px), so it differs from the section landmark (axe landmark-unique).
      <Table aria-labelledby={`${headingId} ${rangeId}`}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              {t('courses.cohorts.sessions.date')}
            </TableHead>
            {named ? (
              <TableHead scope="col">
                {t('courses.cohorts.sessions.students')}
              </TableHead>
            ) : null}
            <TableHead scope="col">
              {t('courses.cohorts.sessions.time')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.cohorts.sessions.state')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.cohorts.sessions.tutor')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.cohorts.sessions.classroom')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.items.map((item) => {
            const Icon = STATE_ICONS[item.state];
            const date = formatLocalDate(
              toLocalDate(item.starts_at, timeZone),
              'long',
            );
            return (
              <TableRow key={item.id}>
                <TableCell>
                  {linkSessions || myScheduleLinks ? (
                    <Link
                      to={
                        linkSessions
                          ? `/schedule/sessions/${encodeURIComponent(item.id)}`
                          : myScheduleHref(item.starts_at, timeZone)
                      }
                      className={TEXT_LINK_CLASS}
                    >
                      {date}
                    </Link>
                  ) : (
                    date
                  )}
                </TableCell>
                {named ? (
                  <TableCell>{namesText(item.students)}</TableCell>
                ) : null}
                <TableCell>
                  {t('courses.cohorts.timeRange', {
                    start: formatInstant(item.starts_at, 'time'),
                    end: formatInstant(item.ends_at, 'time'),
                  })}
                </TableCell>
                <TableCell>
                  <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="inline-flex items-center gap-1">
                      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
                      {sessionStateText(item.state, t, !myScheduleLinks)}
                    </span>
                    {item.is_exception ? (
                      <span className="rounded-md border border-border px-1.5 text-xs font-medium">
                        {t('courses.cohorts.sessions.exception')}
                      </span>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell>
                  {item.tutor?.display_name ??
                    t('courses.cohorts.sessions.noTutor')}
                </TableCell>
                <TableCell>
                  {item.classroom?.name ??
                    t('courses.cohorts.sessions.noClassroom')}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    );
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {t('courses.cohorts.sessions.heading')}
      </h2>
      <p id={rangeId} className="text-sm text-muted-foreground">
        {t('courses.cohorts.sessions.range', {
          from: formatLocalDate(windowStart),
          to: formatLocalDate(last),
        })}{' '}
        {t('scheduling.zone', { zone: timeZone })}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onPrevious}>
          {t('courses.cohorts.sessions.previous')}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onNext}>
          {t('courses.cohorts.sessions.next')}
        </Button>
      </div>
      {content}
    </section>
  );
}
