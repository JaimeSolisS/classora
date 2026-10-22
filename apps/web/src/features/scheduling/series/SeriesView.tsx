import { CircleStop, PencilLine } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { STATE_ICONS } from '@/features/calendar/EventContent';
import { toLanguage } from '@/i18n/language';
import { formatInstant, formatLocalDate, toLocalDate } from '@/lib/time';
import {
  addMinutesToTime,
  localTimeText,
} from '@/features/tutor-portal/offers/rules';
import type { ClassSeriesDetail } from '../session/api';
import type { SeriesOccurrence } from './api';
import { describeRecurrence } from './describeRecurrence';
import { isSeriesOver, seriesActions, seriesPath } from './rules';

// The series page's content (#54b B2, B3), presentational: a header with
// the student, subject, tutor (or "No tutor yet"), room, the time in the
// center zone with the zone shown and the rule summary; "Ended" when the
// series is over; the lineage links ("Continues from…", "Continued as…");
// the occurrences from now on, each with its local date and time, its state
// as text and an icon (never colour only) and "Edited" for an exception.
// Holders of schedule.manage get "Edit this occurrence" on rows that
// haven't started and can still change, "Edit series" and "Cancel future
// occurrences", none of them once the series has ended.

export interface SeriesViewProps {
  series: ClassSeriesDetail;
  timeZone: string;
  canManage: boolean;
  /** The current time (tests and stories). */
  now?: number;
  /** A just-made change, announced at the top. */
  notice?: string | null;
  /** Move the focus to the notice (its trigger may be gone). */
  focusNotice?: boolean;
  onEditOccurrence: (occurrence: SeriesOccurrence) => void;
  onEditSeries: () => void;
  onCancelSeries: () => void;
}

const LINK_CLASS =
  'underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function SeriesView({
  series,
  timeZone,
  canManage,
  now,
  notice,
  focusNotice = false,
  onEditOccurrence,
  onEditSeries,
  onCancelSeries,
}: SeriesViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const noticeRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (notice && focusNotice) {
      noticeRef.current?.focus();
    }
  }, [notice, focusNotice]);
  // The time the page was shown: rows are judged against it (B3).
  const [shownAt] = useState(() => Date.now());
  const over = isSeriesOver(series, timeZone);
  const actions = seriesActions(series, canManage, timeZone, now ?? shownAt);
  const start = localTimeText(series.start_time, language);
  const end = localTimeText(
    addMinutesToTime(series.start_time, series.duration_minutes),
    language,
  );

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {notice ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="rounded-md border border-border p-3 break-words outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {notice}
        </p>
      ) : null}
      <section
        aria-labelledby={`${id}-summary`}
        className="flex flex-col gap-3"
      >
        <div className="flex flex-wrap items-center gap-3">
          <h2 id={`${id}-summary`} className="text-lg font-semibold">
            {t('scheduling.success.subject', {
              subject: series.subject.name,
              level: series.subject.education_level_name,
            })}
          </h2>
          {over ? (
            <span className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs font-medium">
              <CircleStop aria-hidden="true" className="size-3.5" />
              {t('scheduling.series.page.ended')}
            </span>
          ) : null}
        </div>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('scheduling.fields.student')}</dt>
          <dd className="break-words">
            {series.participants.map((p) => p.display_name).join(', ')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.tutor')}</dt>
          <dd className="break-words">
            {series.tutor?.display_name ?? t('scheduling.series.page.noTutor')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.classroom')}</dt>
          <dd className="break-words">
            {series.classroom?.name ?? t('scheduling.noClassroom')}
          </dd>
          <dt className="font-medium">{t('scheduling.series.page.time')}</dt>
          <dd>
            {t('scheduling.series.page.timeValue', {
              start,
              end,
              zone: timeZone,
            })}
          </dd>
          <dt className="font-medium">{t('scheduling.series.rule')}</dt>
          <dd className="break-words">
            {describeRecurrence(series.recurrence, language)}
          </dd>
          <dt className="font-medium">{t('scheduling.series.startsOn')}</dt>
          <dd>{formatLocalDate(series.starts_on, { language })}</dd>
        </dl>
        {series.split_from_series_id || series.split_into_series_ids.length ? (
          <ul className="flex flex-col gap-1 text-sm">
            {series.split_from_series_id ? (
              <li>
                <Link
                  to={seriesPath(series.split_from_series_id)}
                  className={LINK_CLASS}
                >
                  {t('scheduling.series.page.continuesFrom')}
                </Link>
              </li>
            ) : null}
            {series.split_into_series_ids.map((next, index) => (
              <li key={next}>
                <Link to={seriesPath(next)} className={LINK_CLASS}>
                  {series.split_into_series_ids.length > 1
                    ? t('scheduling.series.page.continuedAsNumbered', {
                        number: index + 1,
                      })
                    : t('scheduling.series.page.continuedAs')}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
        {actions.editSeries || actions.cancel ? (
          <div className="flex flex-wrap gap-3">
            <Button type="button" variant="outline" onClick={onEditSeries}>
              {t('scheduling.series.page.editSeries')}
            </Button>
            <Button type="button" variant="outline" onClick={onCancelSeries}>
              {t('scheduling.series.page.cancelSeries')}
            </Button>
          </div>
        ) : null}
      </section>
      <section
        aria-labelledby={`${id}-occurrences`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-occurrences`} className="text-lg font-semibold">
          {t('scheduling.series.page.occurrences')}
        </h2>
        {series.occurrences.length === 0 ? (
          <p className="text-sm">{t('scheduling.series.page.noOccurrences')}</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {series.occurrences.map((occurrence) => {
              const Icon = STATE_ICONS[occurrence.state];
              const date = formatLocalDate(
                toLocalDate(occurrence.starts_at, timeZone),
                { language, style: 'long' },
              );
              const range = t('scheduling.series.page.range', {
                start: formatInstant(occurrence.starts_at, {
                  timeZone,
                  language,
                  style: 'time',
                }),
                end: formatInstant(occurrence.ends_at, {
                  timeZone,
                  language,
                  style: 'time',
                }),
              });
              return (
                <li
                  key={occurrence.id}
                  className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex min-w-0 flex-col gap-1">
                    <p className="font-medium break-words">
                      {date} · {range}
                    </p>
                    <p className="flex flex-wrap items-center gap-2">
                      <span className="inline-flex items-center gap-1">
                        <Icon
                          aria-hidden="true"
                          className="size-3.5 shrink-0"
                        />
                        {t(`scheduling.states.${occurrence.state}`)}
                      </span>
                      {occurrence.is_exception ? (
                        <span className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 text-xs font-medium">
                          <PencilLine
                            aria-hidden="true"
                            className="size-3.5 shrink-0"
                          />
                          {t('scheduling.series.page.edited')}
                        </span>
                      ) : null}
                    </p>
                  </div>
                  {actions.editOccurrence(occurrence) ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={t('scheduling.series.page.editOccurrenceOf', {
                        date,
                        range,
                      })}
                      onClick={() => onEditOccurrence(occurrence)}
                    >
                      {t('scheduling.series.page.editOccurrence')}
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}
