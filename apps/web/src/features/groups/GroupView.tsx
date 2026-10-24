import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { STATE_ICONS } from '@/features/calendar/EventContent';
import { describeRecurrence } from '@/features/scheduling/series/describeRecurrence';
import {
  addMinutesToTime,
  localTimeText,
} from '@/features/tutor-portal/offers/rules';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { toLocalDate } from '@/lib/time';
import type { GroupDetail, GroupEnrollmentItem } from './api';
import { EndedBadge, LINK_CLASS } from './GroupsTable';
import { RosterSection } from './RosterSection';
import { TutorRosterSection } from './TutorRosterSection';
import {
  groupActions,
  isGroupEnded,
  roomLimited,
  type GroupView as View,
} from './rules';

// The group page's content (#234 C1-C3, C5-C7), presentational. Both views
// show the header: the subject and level, the tutor (or "No tutor yet"), the
// room (or "No classroom"), the schedule from `current_series` (the rule
// summary and the time in the center zone, with the zone shown) and "Ended"
// once no series produces sessions. The staff view adds the capacity ("N of
// M seats taken", and the room note when the room holds fewer), the next
// sessions (state as text and an icon; a link to the session page), the
// roster, and for schedule.manage holders "Enroll student" (not once
// ended), "Withdraw" and "Edit". The student view (students, guardians and
// tutors, #246 B1) never renders the roster, the count, `next_occurrences`
// or any control, even if a response carried them (defence in depth): the
// page passes "My sessions" instead, and the group's tutor also gets
// "Students" (`tutor_roster`, names only); families get no roster at all.

export interface GroupViewProps {
  group: GroupDetail;
  view: View;
  timeZone: string;
  canManage: boolean;
  /** A just-made change, announced at the top. */
  notice?: string | null;
  /** Move the focus to the notice (its trigger may be gone). */
  focusNotice?: boolean;
  onEnroll: () => void;
  onWithdraw: (item: GroupEnrollmentItem) => void;
  onEdit: () => void;
  /** The student's "My sessions" section (the student view). */
  mySessions?: ReactNode;
}

export function GroupView({
  group,
  view,
  timeZone,
  canManage,
  notice,
  focusNotice = false,
  onEnroll,
  onWithdraw,
  onEdit,
  mySessions,
}: GroupViewProps) {
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
  const staff = view === 'staff';
  const series = group.current_series;
  const ended = isGroupEnded(group);
  const actions = groupActions(group, staff && canManage);
  const tutorRoster = staff ? null : (group.tutor_roster ?? null);

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
        className="flex flex-col gap-3"
      >
        <div className="flex flex-wrap items-center gap-3">
          <h2 id={`${id}-details`} className="text-lg font-semibold">
            {t('groups.subjectLevel', {
              subject: group.subject.name,
              level: group.subject.level.name,
            })}
          </h2>
          {ended ? <EndedBadge /> : null}
        </div>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('scheduling.fields.tutor')}</dt>
          <dd className="break-words">
            {series?.tutor?.display_name ?? t('groups.noTutor')}
          </dd>
          <dt className="font-medium">{t('scheduling.fields.classroom')}</dt>
          <dd className="break-words">
            {series?.classroom?.name ?? t('scheduling.noClassroom')}
          </dd>
          {series ? (
            <>
              <dt className="font-medium">{t('groups.page.schedule')}</dt>
              <dd className="break-words">
                {describeRecurrence(series.recurrence, language)}
              </dd>
              <dt className="font-medium">{t('groups.page.time')}</dt>
              <dd>
                {t('groups.page.timeValue', {
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
              <dt className="font-medium">{t('scheduling.series.startsOn')}</dt>
              <dd>{formatLocalDate(series.starts_on)}</dd>
            </>
          ) : null}
        </dl>
        {actions.edit || actions.enroll ? (
          <div className="flex flex-wrap gap-3">
            {actions.enroll ? (
              <Button type="button" onClick={onEnroll}>
                {t('groups.page.enroll')}
              </Button>
            ) : null}
            {actions.edit ? (
              <Button type="button" variant="outline" onClick={onEdit}>
                {t('groups.page.edit')}
              </Button>
            ) : null}
          </div>
        ) : null}
      </section>
      {staff ? (
        <>
          <section
            aria-labelledby={`${id}-capacity`}
            className="flex flex-col gap-2"
          >
            <h2 id={`${id}-capacity`} className="text-lg font-semibold">
              {t('groups.capacity.heading')}
            </h2>
            <p>
              {t('groups.capacity.taken', {
                enrolled: group.enrolled_count ?? 0,
                max: group.max_students,
              })}
            </p>
            {roomLimited(group) ? (
              <p className="text-sm">
                {t('groups.capacity.roomLimit', {
                  capacity: group.effective_capacity,
                })}
              </p>
            ) : null}
          </section>
          <section
            aria-labelledby={`${id}-next`}
            className="flex flex-col gap-3"
          >
            <h2 id={`${id}-next`} className="text-lg font-semibold">
              {t('groups.next.heading')}
            </h2>
            {group.next_occurrences.length === 0 ? (
              <p className="text-sm">{t('groups.next.none')}</p>
            ) : (
              <ol
                aria-labelledby={`${id}-next`}
                className="flex flex-col gap-2"
              >
                {group.next_occurrences.map((occurrence) => {
                  const Icon = STATE_ICONS[occurrence.state];
                  const when = `${formatLocalDate(
                    toLocalDate(occurrence.starts_at, timeZone),
                    'long',
                  )} · ${t('groups.timeRange', {
                    start: formatInstant(occurrence.starts_at, 'time'),
                    end: formatInstant(occurrence.ends_at, 'time'),
                  })}`;
                  return (
                    <li
                      key={occurrence.id}
                      className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
                    >
                      <Link
                        to={`/schedule/sessions/${encodeURIComponent(occurrence.id)}`}
                        className={LINK_CLASS}
                      >
                        {when}
                      </Link>
                      <span className="inline-flex items-center gap-1">
                        <Icon
                          aria-hidden="true"
                          className="size-3.5 shrink-0"
                        />
                        {t(`scheduling.states.${occurrence.state}`)}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
          <RosterSection
            roster={group.roster ?? []}
            timeZone={timeZone}
            onWithdraw={actions.withdraw ? onWithdraw : undefined}
          />
        </>
      ) : (
        <>
          {tutorRoster ? <TutorRosterSection roster={tutorRoster} /> : null}
          {mySessions}
        </>
      )}
    </div>
  );
}
