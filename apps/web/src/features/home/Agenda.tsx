import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import type { CalendarItem } from '@/features/calendar/api';
import { KindBadge } from '@/features/calendar/KindBadge';
import {
  sessionTimeText,
  tutorText,
  type LabelContext,
} from '@/features/calendar/labels';
import type { FamilyStudent } from '@/features/my-schedule/api';
import { roomText, stateLabel, whoText } from '@/features/my-schedule/labels';
import { TEACHING } from '@/features/my-schedule/whose';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { toLocalDate, type LocalDate } from '@/lib/time';
import {
  HomeCard,
  LINK_CLASS,
  NOTE_CLASS,
  ROW_LINK_CLASS,
  type HeadingLevel,
} from './Card';
import {
  readableStudents,
  useFamilySchedules,
  useFamilyStudents,
  useStaffSessions,
  useTutorSchedule,
} from './queries';
import {
  MAX_FAMILY_STUDENTS,
  personalAgenda,
  STAFF_TODAY_ROWS,
  staffAgenda,
  type AgendaRow,
  type Window,
} from './rules';

// The agenda sections of Home (HF-06 B1–B5): the center's classes today
// (staff), "My classes" (tutor) and the family's classes (student or
// guardian). Each section loads, fails and retries on its own; a failure in
// one leaves the others in place. Times are in the center zone. HF-07: every
// row starts with the class type (swatch, icon and text, the calendar's
// colours) and names the tutor.

interface AgendaProps {
  today: LocalDate;
  timeZone: string;
  window: Window;
  level: HeadingLevel;
}

function useLabelContext(timeZone: string): LabelContext {
  const { t, i18n } = useTranslation();
  return { t, timeZone, language: toLanguage(i18n.resolvedLanguage) };
}

/** "and 3 more" / "and 3+ more". */
function moreText(count: number, truncated: boolean) {
  return { count, shown: truncated ? `${count}+` : `${count}` };
}

// ------------------------------------------------------------- staff (B1)

function StaffRow({
  item,
  context,
}: {
  item: CalendarItem;
  context: LabelContext;
}) {
  const { t } = context;
  const details = [
    tutorText(item, context),
    item.classroom?.name,
    t(`scheduling.states.${item.state}`),
  ].filter((part): part is string => Boolean(part));
  return (
    <li>
      <Link
        to={`/schedule/sessions/${item.id}`}
        className={ROW_LINK_CLASS}
        data-home-session={item.id}
      >
        <span className="text-sm font-medium">
          <KindBadge kind={item.kind} swatch className="font-normal" />{' '}
          <span className="tabular-nums">{sessionTimeText(item, context)}</span>{' '}
          {item.subject.name}
        </span>
        <span className={NOTE_CLASS}>{details.join(' · ')}</span>
      </Link>
    </li>
  );
}

export function StaffAgendaSection({
  today,
  timeZone,
  window,
  level,
}: AgendaProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const context = useLabelContext(timeZone);
  const sessions = useStaffSessions(window, true);

  let body;
  if (sessions.isPending) {
    body = <LoadingState rows={3} label={t('home.staff.loading')} />;
  } else if (sessions.isError) {
    body = (
      <ErrorAlert
        error={sessions.error}
        onRetry={() => void sessions.refetch()}
      />
    );
  } else {
    const agenda = staffAgenda(sessions.data.items, today, timeZone);
    const rest = agenda.today.length - STAFF_TODAY_ROWS;
    body =
      agenda.today.length === 0 ? (
        <div className="flex flex-col gap-1" data-home-empty>
          <p className="text-sm">{t('home.staff.empty')}</p>
          <p className={NOTE_CLASS}>
            {agenda.next
              ? t('home.next', {
                  when: formatInstant(agenda.next.starts_at, 'dateTime'),
                  subject: agenda.next.subject.name,
                })
              : t('home.staff.none')}
          </p>
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-1">
            {agenda.today.slice(0, STAFF_TODAY_ROWS).map((item) => (
              <StaffRow key={item.id} item={item} context={context} />
            ))}
          </ul>
          {rest > 0 ? (
            <p className={NOTE_CLASS} data-home-more>
              {t('home.more', moreText(rest, sessions.data.truncated))}
            </p>
          ) : null}
        </>
      );
  }

  return (
    <HomeCard
      title={t('home.staff.title')}
      level={level}
      data-home-section="staff"
    >
      {body}
      <Link to={`/schedule?view=day&date=${today}`} className={LINK_CLASS}>
        {t('home.staff.calendar')}
      </Link>
    </HomeCard>
  );
}

// ------------------------------------------------ tutor and family (B2, B3)

function PersonalRow({
  row,
  today,
  context,
  tutorName,
}: {
  row: AgendaRow;
  today: LocalDate;
  context: LabelContext;
  /** A tutor's own rows: the signed-in tutor's name (HF-07). */
  tutorName?: string;
}) {
  const { t } = context;
  const { formatLocalDate } = useFormatters();
  const { item } = row.entry;
  const date = toLocalDate(item.starts_at, context.timeZone);
  const whose = row.studentId ?? TEACHING;
  const details = [
    row.studentName ? t('home.forStudent', { name: row.studentName }) : null,
    // A family's who is already "Tutor: …"; a tutor's is the students.
    tutorName === undefined
      ? null
      : t('scheduling.calendar.tutorLine', { name: tutorName }),
    whoText(row.entry, t),
    roomText(row.entry, t),
    stateLabel(row.entry, t),
  ].filter((part): part is string => Boolean(part));
  return (
    <li>
      <Link
        to={`/my-schedule?for=${whose}&view=day&date=${date}`}
        className={ROW_LINK_CLASS}
        data-home-session={item.id}
      >
        <span className="text-sm font-medium">
          <KindBadge kind={item.kind} swatch className="font-normal" />{' '}
          <span className="tabular-nums">
            {date === today ? null : `${formatLocalDate(date)}, `}
            {sessionTimeText(item, context)}
          </span>{' '}
          {item.subject.name}
        </span>
        <span className={NOTE_CLASS}>{details.join(' · ')}</span>
      </Link>
    </li>
  );
}

function PersonalList({
  rows,
  today,
  timeZone,
  tutorName,
}: {
  rows: readonly AgendaRow[];
  today: LocalDate;
  timeZone: string;
  tutorName?: string;
}) {
  const { t } = useTranslation();
  const context = useLabelContext(timeZone);
  const agenda = personalAgenda(rows, today, timeZone);
  return (
    <>
      {agenda.today.length === 0 ? (
        <p className="text-sm" data-home-empty>
          {t('home.personal.empty')}
        </p>
      ) : (
        <ul className="flex flex-col gap-1" data-home-today-list>
          {agenda.today.map((row) => (
            <PersonalRow
              key={`${row.studentId ?? ''}:${row.entry.item.id}`}
              row={row}
              today={today}
              context={context}
              tutorName={tutorName}
            />
          ))}
        </ul>
      )}
      {agenda.upcoming.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className={NOTE_CLASS}>{t('home.personal.upcoming')}</p>
          <ul className="flex flex-col gap-1" data-home-upcoming-list>
            {agenda.upcoming.map((row) => (
              <PersonalRow
                key={`${row.studentId ?? ''}:${row.entry.item.id}`}
                row={row}
                today={today}
                context={context}
                tutorName={tutorName}
              />
            ))}
          </ul>
        </div>
      ) : agenda.today.length === 0 ? (
        <p className={NOTE_CLASS}>{t('home.personal.none')}</p>
      ) : null}
    </>
  );
}

function ScheduleLink({ today }: { today: LocalDate }) {
  const { t } = useTranslation();
  return (
    <Link to={`/my-schedule?view=day&date=${today}`} className={LINK_CLASS}>
      {t('home.personal.schedule')}
    </Link>
  );
}

export function TutorAgendaSection({
  userId,
  userName,
  today,
  timeZone,
  window,
  level,
}: AgendaProps & { userId: string; userName: string }) {
  const { t } = useTranslation();
  const schedule = useTutorSchedule(userId, window, true);
  return (
    <HomeCard
      title={t('home.tutor.title')}
      level={level}
      data-home-section="tutor"
    >
      {schedule.isPending ? (
        <LoadingState rows={3} label={t('home.personal.loading')} />
      ) : schedule.isError ? (
        <ErrorAlert
          error={schedule.error}
          onRetry={() => void schedule.refetch()}
        />
      ) : (
        <PersonalList
          rows={schedule.data.entries.map((entry) => ({
            entry,
            studentId: null,
            studentName: null,
          }))}
          today={today}
          timeZone={timeZone}
          tutorName={userName}
        />
      )}
      <ScheduleLink today={today} />
    </HomeCard>
  );
}

function isConsentRequired(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'student_consent_required';
}

export function FamilyAgendaSection({
  today,
  timeZone,
  window,
  level,
}: AgendaProps) {
  const { t } = useTranslation();
  const family = useFamilyStudents(true);
  const students: readonly FamilyStudent[] = family.data?.items ?? [];
  const readable = readableStudents(students, MAX_FAMILY_STUDENTS);
  const schedules = useFamilySchedules(readable, window);
  // The consent was revoked meanwhile: the same line as a consent student.
  const revoked = readable.filter((_, index) =>
    isConsentRequired(schedules[index]?.error),
  );
  const consent = [
    ...students.filter(
      (student) => student.schedule_access === 'consent_required',
    ),
    ...revoked,
  ];
  const failed = schedules.filter(
    (query) => query.isError && !isConsentRequired(query.error),
  );
  const several = readable.length > 1;
  const rows: AgendaRow[] = readable.flatMap((student, index) =>
    (schedules[index]?.data?.entries ?? []).map((entry) => ({
      entry,
      studentId: student.student_id,
      studentName: several ? student.display_name : null,
    })),
  );

  let body;
  if (family.isPending || schedules.some((query) => query.isPending)) {
    body = <LoadingState rows={3} label={t('home.personal.loading')} />;
  } else if (family.isError) {
    body = (
      <ErrorAlert error={family.error} onRetry={() => void family.refetch()} />
    );
  } else {
    body = (
      <>
        {failed.length > 0 ? (
          <ErrorAlert
            error={failed[0]!.error}
            onRetry={() => failed.forEach((query) => void query.refetch())}
          />
        ) : null}
        {consent.length > 0 ? (
          <ul className="flex flex-col gap-1" data-home-consent>
            {consent.map((student) => (
              <li key={student.student_id} className={NOTE_CLASS}>
                {t('home.family.consent', { name: student.display_name })}
              </li>
            ))}
          </ul>
        ) : null}
        <PersonalList rows={rows} today={today} timeZone={timeZone} />
      </>
    );
  }

  return (
    <HomeCard
      title={t('home.family.title')}
      level={level}
      data-home-section="family"
    >
      {body}
      <ScheduleLink today={today} />
    </HomeCard>
  );
}
