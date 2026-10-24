import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { STATE_ICONS } from '@/features/calendar/EventContent';
import { describeRecurrence } from '@/features/scheduling/series/describeRecurrence';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import {
  addMinutesToTime,
  localTimeText,
} from '@/features/tutor-portal/offers/rules';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { toLocalDate } from '@/lib/time';
import { CourseSyllabus } from '../CourseSyllabus';
import type { CohortDetail } from './api';
import { assignmentOf, cohortRule, roomLimited } from './rules';

// The cohort page's content (#243 D1-D4), presentational, in sections the
// page composes: the details (template and version, subject and level,
// maximum and effective capacity, status as text, planned sessions, first
// and last session dates, the rule summary, the time in the center zone
// and the room), "Edit" for schedule.manage holders, the tutor assignment
// (with the page's offer panel while the series has no tutor), the next
// sessions (state as text and an icon; a link to the session page for
// schedule.view holders), then `children` (the page's "Sessions" range
// list and the #245 Students panel) and the syllabus from
// the cohort's snapshot, never the live template.

export interface CohortViewProps {
  cohort: CohortDetail;
  timeZone: string;
  canManage: boolean;
  /** Link the template (course.view) and the sessions (schedule.view). */
  linkTemplate: boolean;
  linkSessions: boolean;
  /** A just-made change, announced at the top. */
  notice?: string | null;
  /** Move the focus to the notice. */
  focusNotice?: boolean;
  onEdit: () => void;
  /** "Offer to a tutor" (the page shows it while the series has none). */
  offerPanel?: ReactNode;
  /** More sections, after the next sessions. */
  children?: ReactNode;
}

export function CohortView({
  cohort,
  timeZone,
  canManage,
  linkTemplate,
  linkSessions,
  notice,
  focusNotice = false,
  onEdit,
  offerPanel,
  children,
}: CohortViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const { formatInstant, formatLocalDate } = useFormatters();
  const id = useId();
  const noticeRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (notice && focusNotice) {
      noticeRef.current?.focus();
    }
  }, [notice, focusNotice]);
  const series = cohort.series;
  const assignment = assignmentOf(series);
  const templateText = t('courses.cohorts.page.templateValue', {
    name: cohort.template.name,
    version: cohort.template.version,
  });
  const date = (value: string | null) =>
    value ? formatLocalDate(value) : t('courses.cohorts.list.noDate');

  let assignmentText: string;
  if (assignment.kind === 'accepted') {
    assignmentText = assignment.tutor;
  } else if (assignment.kind === 'pending') {
    assignmentText = assignment.respondBy
      ? t('courses.cohorts.assignment.respondsBy', {
          name: assignment.tutor,
          date: formatInstant(assignment.respondBy, 'dateTime'),
        })
      : t('courses.cohorts.assignment.pendingTutor', {
          name: assignment.tutor,
        });
  } else {
    assignmentText = t('courses.cohorts.assignment.noTutor');
  }

  return (
    <div className="flex w-full max-w-4xl min-w-0 flex-col gap-8">
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
        aria-labelledby={`${id}-details`}
        className="flex min-w-0 flex-col gap-3"
      >
        <h2 id={`${id}-details`} className="text-lg font-semibold">
          {t('courses.cohorts.page.details')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('courses.cohorts.page.template')}</dt>
          <dd className="break-words">
            {linkTemplate ? (
              <Link
                to={`/courses/templates/${encodeURIComponent(cohort.template.id)}`}
                className={TEXT_LINK_CLASS}
              >
                {templateText}
              </Link>
            ) : (
              templateText
            )}
          </dd>
          <dt className="font-medium">{t('courses.cohorts.page.subject')}</dt>
          <dd className="break-words">
            {t('courses.cohorts.subjectLevel', {
              subject: cohort.subject.name,
              level: cohort.subject.level.name,
            })}
          </dd>
          <dt className="font-medium">{t('courses.cohorts.page.status')}</dt>
          <dd>{t(`courses.cohorts.status.${cohort.status}`)}</dd>
          <dt className="font-medium">{t('courses.cohorts.page.max')}</dt>
          <dd>{cohort.max_students}</dd>
          <dt className="font-medium">{t('courses.cohorts.page.capacity')}</dt>
          <dd>
            {roomLimited(cohort)
              ? t('courses.cohorts.page.roomLimited', {
                  capacity: cohort.effective_capacity,
                })
              : cohort.effective_capacity}
          </dd>
          <dt className="font-medium">{t('courses.cohorts.page.planned')}</dt>
          <dd>
            {t('courses.cohorts.create.templateSessions', {
              count: cohort.planned_session_count,
              minutes: cohort.session_minutes,
            })}
          </dd>
          <dt className="font-medium">{t('courses.cohorts.page.first')}</dt>
          <dd>{date(cohort.first_session_on)}</dd>
          <dt className="font-medium">{t('courses.cohorts.page.last')}</dt>
          <dd>{date(cohort.last_session_on)}</dd>
          {series ? (
            <>
              <dt className="font-medium">
                {t('courses.cohorts.page.schedule')}
              </dt>
              <dd className="break-words">
                {describeRecurrence(
                  cohortRule(series.recurrence, cohort.planned_session_count),
                  language,
                )}
              </dd>
              <dt className="font-medium">{t('courses.cohorts.page.time')}</dt>
              <dd className="break-words">
                {t('courses.cohorts.page.timeValue', {
                  start: localTimeText(series.start_time, language),
                  end: localTimeText(
                    addMinutesToTime(
                      series.start_time,
                      series.duration_minutes,
                    ),
                    language,
                  ),
                  zone: timeZone,
                })}
              </dd>
              <dt className="font-medium">
                {t('courses.cohorts.page.classroom')}
              </dt>
              <dd className="break-words">
                {series.classroom?.name ?? t('scheduling.noClassroom')}
              </dd>
            </>
          ) : null}
        </dl>
        {canManage ? (
          <div>
            <Button type="button" variant="outline" onClick={onEdit}>
              {t('courses.cohorts.page.edit')}
            </Button>
          </div>
        ) : null}
      </section>
      <section
        aria-labelledby={`${id}-tutor`}
        className="flex min-w-0 flex-col gap-3"
      >
        <h2 id={`${id}-tutor`} className="text-lg font-semibold">
          {t('courses.cohorts.tutor.heading')}
        </h2>
        <p className="break-words">{assignmentText}</p>
        {offerPanel}
      </section>
      <section
        aria-labelledby={`${id}-next`}
        className="flex min-w-0 flex-col gap-3"
      >
        <h2 id={`${id}-next`} className="text-lg font-semibold">
          {t('courses.cohorts.next.heading')}
        </h2>
        {cohort.next_occurrences.length === 0 ? (
          <p className="text-sm">{t('courses.cohorts.next.none')}</p>
        ) : (
          <ol aria-labelledby={`${id}-next`} className="flex flex-col gap-2">
            {cohort.next_occurrences.map((occurrence) => {
              const Icon = STATE_ICONS[occurrence.state];
              const when = `${formatLocalDate(
                toLocalDate(occurrence.starts_at, timeZone),
                'long',
              )} · ${t('courses.cohorts.timeRange', {
                start: formatInstant(occurrence.starts_at, 'time'),
                end: formatInstant(occurrence.ends_at, 'time'),
              })}`;
              return (
                <li
                  key={occurrence.id}
                  className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                >
                  {linkSessions ? (
                    <Link
                      to={`/schedule/sessions/${encodeURIComponent(occurrence.id)}`}
                      className={TEXT_LINK_CLASS}
                    >
                      {when}
                    </Link>
                  ) : (
                    <span className="break-words">{when}</span>
                  )}
                  <span className="inline-flex items-center gap-1">
                    <Icon aria-hidden="true" className="size-3.5 shrink-0" />
                    {t(`scheduling.states.${occurrence.state}`)}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </section>
      {children}
      <section
        aria-labelledby={`${id}-syllabus`}
        className="flex min-w-0 flex-col gap-6"
      >
        <div className="flex flex-col gap-2">
          <h2 id={`${id}-syllabus`} className="text-lg font-semibold">
            {t('courses.cohorts.syllabus.heading')}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t('courses.cohorts.syllabus.help')}
          </p>
        </div>
        <CourseSyllabus
          modules={cohort.syllabus.modules}
          assessments={cohort.syllabus.assessments}
          completion={cohort.syllabus.completion}
          subjectName={new Map([[cohort.subject.id, cohort.subject.name]])}
          headingLevel={3}
        />
      </section>
    </div>
  );
}
