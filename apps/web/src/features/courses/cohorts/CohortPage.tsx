import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan, useSession } from '@/features/auth/useSession';
import { CALENDAR_KEY } from '@/features/calendar/api';
import {
  fetchClassroomCapacityOptions,
  GROUP_OPTIONS_KEY,
} from '@/features/groups/api';
import { EnrollDialog } from '@/features/groups/EnrollDialog';
import { WithdrawDialog } from '@/features/groups/WithdrawDialog';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import type { WithdrawalConfirmed } from '@/features/billing/withdrawals/api';
import {
  useCanSettleEnrollments,
  useUnsettledEnrollments,
} from '@/features/billing/withdrawals/useSettlement';
import { WithdrawalFlowDialog } from '@/features/billing/withdrawals/WithdrawalDialog';
import {
  fetchTutorOptions,
  SCHEDULE_OPTIONS_KEY,
  searchStudents,
  type TutorSuggestionList,
} from '@/features/scheduling/create/api';
import {
  fetchReplacementTutors,
  offerClassSeries,
} from '@/features/scheduling/session/api';
import { OfferPanel } from '@/features/scheduling/session/OfferPanel';
import {
  seriesOfferBody,
  type OfferValues,
} from '@/features/scheduling/session/rules';
import { useFormatters } from '@/lib/formatters';
import { todayInZone } from '@/lib/time';
import { TutorRosterSection } from '@/features/groups/TutorRosterSection';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { CoursesTabs } from '../CoursesTabs';
import {
  cohortKey,
  COHORTS_KEY,
  cohortSessionsKey,
  enrollInCohort,
  fetchCohort,
  fetchCohortSessions,
  updateCohort,
  withdrawFromCohort,
  type CohortEnrollmentItem,
} from './api';
import {
  CohortSessionsSection,
  type CohortSessionsState,
} from './CohortSessionsSection';
import { CohortCompletion } from './completion/CohortCompletion';
import { completionAudience, familyHeading } from './completion/rules';
import { CohortView } from './CohortView';
import { EditCohortDialog } from './EditCohortDialog';
import { useEnrollLabels, useWithdrawLabels } from './enrollmentLabels';
import { RosterPanel } from './RosterPanel';
import {
  canOfferSeries,
  cohortWithdrawBody,
  enrollBody,
  enrolledStudentIds,
  enrollmentControls,
  offerFromDate,
  roomLimited,
  sessionsWindow,
  shiftWindow,
  withdrawnShown,
} from './rules';

// /courses/cohorts/:cohortId (#243 D), for course.view holders: the
// details, the tutor assignment, the next sessions, the sessions of a
// 4-week window and the syllabus (the cohort's snapshot). schedule.manage
// holders edit the name and maximum; schedule.assign holders (Coordinators
// too) offer the series to a tutor while it has none, from today (or its
// start) with the #51 suggestions for its next session. An unknown id (404)
// is the shared not-found page. Every write refreshes the cohort (and the
// list and the calendar) and is announced at the top.
//
// The Students section (#245) shows when the detail carries a `roster`
// (course.view holders, #66 F1); "Show withdrawn" is `?withdrawn=1`.
//
// A tutor, student or guardian (#246 B2, no course.view) gets the same page
// read-only (every control depends on a permission they don't hold): the
// details, the sessions they may see (linked to "My schedule", B4), the
// syllabus, and for the cohort's tutor "Students" (`tutor_roster`).
// schedule.manage holders enroll (the #234 dialog with the cohort's texts:
// `{student_id, override?: {codes, reason}}`) and withdraw (`{version,
// reason?}`). An already_enrolled or cohort_ended answer refreshes the
// cohort and stays in the dialog; a withdrawal's version_conflict,
// enrollment_not_active or enrollment_not_found closes it, refreshes the
// roster and says so at the top.
//
// Course completion (#323) follows the Students panel: the roster for
// evaluation.manage holders and the cohort's tutor, the family's own
// sections for a student or guardian, nothing (and no request) for anyone
// else.

// #387 B2: with schedule.manage and billing.view (and billing.manage or
// finance.refund.approve), Withdraw is the two-step dialog (the reason,
// then the settlement that withdraws and settles in one call), and a shown
// withdrawn row with no withdrawal settlement gets "Settle billing"; a
// Coordinator keeps the plain withdrawal.

/** The withdrawal answers that mean "the roster you saw is stale" (C2). */
const STALE_WITHDRAWAL = new Set([
  'version_conflict',
  'enrollment_not_active',
  'enrollment_not_found',
]);

interface PageNotice {
  notice?: string;
}

const NO_SUGGESTIONS: TutorSuggestionList = { items: [], eligible_count: 0 };

export function CohortPage() {
  const { cohortId = '' } = useParams();
  return <CohortPageContent key={cohortId} cohortId={cohortId} />;
}

function CohortPageContent({ cohortId }: { cohortId: string }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  const timeZone = useCenterTimeZone();
  const { formatLocalDate } = useFormatters();
  const canManage = useCan('schedule.manage');
  const canAssign = useCan('schedule.assign');
  const canViewSchedule = useCan('schedule.view');
  const canViewCourses = useCan('course.view');
  const canOverride = useCan('schedule.override');
  const canViewStudents = useCan('student.view');
  const canEvaluate = useCan('evaluation.manage');
  const canSettle = useCanSettleEnrollments();
  const money = useAccountLabels();
  const session = useSession();
  const roles = session.status === 'signedIn' ? session.user.roles : [];
  const client = useQueryClient();
  const [notice, setNotice] = useState<string | null>(
    (location.state as PageNotice | null)?.notice ?? null,
  );
  const [noticeFocus, setNoticeFocus] = useState(notice !== null);
  const [editing, setEditing] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [withdrawing, setWithdrawing] = useState<CohortEnrollmentItem | null>(
    null,
  );
  const [settling, setSettling] = useState<{
    item: CohortEnrollmentItem;
    withdraw: boolean;
  } | null>(null);
  const [windowStart, setWindowStart] = useState<string>(() =>
    todayInZone(timeZone),
  );

  const cohort = useQuery({
    queryKey: cohortKey(cohortId),
    queryFn: ({ signal }) => fetchCohort(cohortId, signal),
  });
  const withdrawnIds = (cohort.data?.roster ?? [])
    .filter((item) => item.withdrawn_at !== null)
    .map((item) => item.id);
  const settleIds = useUnsettledEnrollments(
    'cohort_enrollment',
    withdrawnIds,
    canSettle && withdrawnShown(search),
  );
  const enrollLabels = useEnrollLabels(cohort.data?.name ?? '');
  const withdrawLabels = useWithdrawLabels(cohort.data?.name ?? '');
  useDocumentTitle(cohort.data?.name ?? t('courses.cohorts.page.title'));
  const range = sessionsWindow(windowStart, timeZone);
  const sessions = useQuery({
    queryKey: cohortSessionsKey(cohortId, range.from, range.to),
    queryFn: ({ signal }) =>
      fetchCohortSessions(cohortId, range.from, range.to, signal),
    enabled: cohort.isSuccess,
  });
  const series = cohort.data?.series ?? null;
  const offering = canOfferSeries(series, canAssign);
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
    enabled: offering,
  });
  const classroomId = series?.classroom?.id ?? null;
  const needsRoom =
    editing &&
    classroomId !== null &&
    cohort.data !== undefined &&
    !roomLimited(cohort.data);
  const classrooms = useQuery({
    queryKey: [...GROUP_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomCapacityOptions(signal),
    enabled: needsRoom,
  });

  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: COHORTS_KEY }),
      client.invalidateQueries({ queryKey: CALENDAR_KEY }),
    ]);
  }

  function announce(text: string, focus: boolean) {
    setNoticeFocus(focus);
    setNotice(text);
  }

  if (cohort.isError) {
    if (cohort.error instanceof ApiError && cohort.error.status === 404) {
      return <NotFoundPage />;
    }
    return (
      <div className="flex flex-col gap-6">
        <CoursesTabs />
        <h1 className="text-2xl font-semibold">
          {t('courses.cohorts.page.title')}
        </h1>
        <ErrorAlert
          error={cohort.error}
          onRetry={() => void cohort.refetch()}
        />
      </div>
    );
  }

  const detail = cohort.data;
  if (!detail) {
    return (
      <div className="flex flex-col gap-6">
        <CoursesTabs />
        <h1 className="text-2xl font-semibold">
          {t('courses.cohorts.page.title')}
        </h1>
        <LoadingState />
      </div>
    );
  }

  const roomCapacity = classroomId
    ? (classrooms.data?.find((room) => room.id === classroomId)?.capacity ??
      null)
    : null;

  const sessionsState: CohortSessionsState = sessions.data
    ? { status: 'ready', items: sessions.data.items }
    : sessions.isError
      ? {
          status: 'error',
          error: sessions.error,
          onRetry: () => void sessions.refetch(),
        }
      : { status: 'loading' };

  let offerPanel = null;
  if (offering && series && tutors.isError) {
    offerPanel = (
      <ErrorAlert error={tutors.error} onRetry={() => void tutors.refetch()} />
    );
  } else if (offering && series && !tutors.data) {
    offerPanel = <LoadingState rows={2} />;
  } else if (offering && series && tutors.data) {
    const fromDate = offerFromDate(series, todayInZone(timeZone));
    const offerSeries = (values: OfferValues) =>
      offerClassSeries(
        series.id,
        seriesOfferBody(values, series.version, fromDate, timeZone),
      );
    const first = detail.next_occurrences[0];
    offerPanel = (
      <OfferPanel
        key={series.version}
        tutors={tutors.data}
        timeZone={timeZone}
        canOverride={false}
        onSuggest={() =>
          first
            ? fetchReplacementTutors(first.id)
            : Promise.resolve(NO_SUGGESTIONS)
        }
        onSubmit={offerSeries}
        series={{
          onSubmit: offerSeries,
          only: {
            help: t('courses.cohorts.tutor.offerHelp', {
              date: formatLocalDate(fromDate),
            }),
            noOverride: t('courses.cohorts.tutor.noOverride'),
            submit: t('courses.cohorts.tutor.submit'),
          },
        }}
        onOffered={() => {
          // The panel goes away: the notice takes the focus.
          announce(t('courses.cohorts.tutor.offered'), true);
          void refresh();
        }}
      />
    );
  }

  const roster = detail.roster ?? null;
  // #323 W0: decided once the detail has loaded; null sends no request.
  const audience = completionAudience({
    canEvaluate,
    hasTutorRoster: Array.isArray(detail.tutor_roster),
    roles,
  });
  const controls = enrollmentControls(detail, canManage);

  function showWithdrawn(shown: boolean) {
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        if (shown) {
          next.set('withdrawn', '1');
        } else {
          next.delete('withdrawn');
        }
        return next;
      },
      { replace: true },
    );
  }

  async function withdraw(item: CohortEnrollmentItem, reason: string) {
    try {
      await withdrawFromCohort(
        cohortId,
        item.id,
        cohortWithdrawBody(item.version, reason),
      );
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.code &&
        STALE_WITHDRAWAL.has(caught.code)
      ) {
        setWithdrawing(null);
        announce(
          caught.code === 'version_conflict'
            ? t('courses.cohorts.enrollment.withdrawDialog.versionConflict')
            : caught.code === 'enrollment_not_active'
              ? tErrors('enrollment_not_active')
              : t('courses.cohorts.enrollment.withdrawDialog.notFound'),
          true,
        );
        await refresh();
        return;
      }
      throw caught;
    }
    setWithdrawing(null);
    // The row's button is gone: the notice takes the focus.
    announce(
      t('courses.cohorts.enrollment.withdrawDialog.done', {
        name: item.student.display_name,
      }),
      true,
    );
    await refresh();
  }

  return (
    <div className="flex flex-col gap-6">
      <CoursesTabs />
      <h1 className="text-2xl font-semibold break-words">{detail.name}</h1>
      <CohortView
        cohort={detail}
        timeZone={timeZone}
        canManage={canManage}
        linkTemplate={canViewCourses}
        linkSessions={canViewSchedule}
        myScheduleLinks={!canViewCourses}
        notice={notice}
        focusNotice={noticeFocus}
        onEdit={() => {
          setNotice(null);
          setEditing(true);
        }}
        offerPanel={offerPanel}
      >
        <CohortSessionsSection
          windowStart={windowStart}
          state={sessionsState}
          timeZone={timeZone}
          linkSessions={canViewSchedule}
          myScheduleLinks={!canViewCourses}
          onPrevious={() => setWindowStart((d) => shiftWindow(d, -1))}
          onNext={() => setWindowStart((d) => shiftWindow(d, 1))}
        />
        {!roster && detail.tutor_roster ? (
          <TutorRosterSection roster={detail.tutor_roster} />
        ) : null}
        {roster ? (
          <RosterPanel
            cohort={detail}
            roster={roster}
            timeZone={timeZone}
            canManage={canManage}
            linkStudents={canViewStudents}
            showWithdrawn={withdrawnShown(search)}
            onShowWithdrawnChange={showWithdrawn}
            onEnroll={() => {
              setNotice(null);
              setEnrolling(true);
            }}
            onWithdraw={(item) => {
              setNotice(null);
              if (canSettle) {
                setSettling({ item, withdraw: true });
              } else {
                setWithdrawing(item);
              }
            }}
            settleIds={canSettle ? settleIds : undefined}
            onSettle={
              canSettle && canManage
                ? (item) => {
                    setNotice(null);
                    setSettling({ item, withdraw: false });
                  }
                : undefined
            }
          />
        ) : null}
        {audience ? (
          <CohortCompletion
            cohortId={cohortId}
            audience={audience}
            familyHeading={familyHeading(roles)}
          />
        ) : null}
      </CohortView>
      {roster && canManage ? (
        <>
          <EnrollDialog
            open={enrolling}
            groupName={detail.name}
            enrolledIds={enrolledStudentIds(roster)}
            searchStudents={searchStudents}
            canOverride={canOverride}
            help={
              controls.startedHelp
                ? t('courses.cohorts.enrollment.dialog.started')
                : undefined
            }
            labels={enrollLabels}
            onEnroll={async (student, override) => {
              try {
                return await enrollInCohort(
                  cohortId,
                  enrollBody(student.id, override),
                );
              } catch (caught) {
                // B2, B4: the roster or the status moved on; the dialog
                // shows the message.
                if (
                  caught instanceof ApiError &&
                  (caught.code === 'already_enrolled' ||
                    caught.code === 'cohort_ended')
                ) {
                  void refresh();
                }
                throw caught;
              }
            }}
            onDone={(_created, student) => {
              setEnrolling(false);
              announce(
                t('courses.cohorts.enrollment.dialog.done', {
                  name: student.name,
                }),
                false,
              );
              void refresh();
            }}
            onCancel={() => setEnrolling(false)}
          />
          {canSettle ? (
            <WithdrawalFlowDialog
              subject={
                settling
                  ? { kind: 'cohort_enrollment', id: settling.item.id }
                  : null
              }
              title={
                settling?.withdraw
                  ? withdrawLabels.title
                  : t('billing.withdrawals.settle.title')
              }
              subjectName={detail.name}
              firstStep={
                settling?.withdraw
                  ? {
                      description: withdrawLabels.description(
                        settling.item.student.display_name,
                      ),
                      help: withdrawLabels.help,
                      reason: t('billing.withdrawals.fields.reason'),
                      keep: withdrawLabels.keep,
                      next: t('billing.withdrawals.dialog.next'),
                    }
                  : undefined
              }
              onDone={(confirmed: WithdrawalConfirmed) => {
                const name = settling?.item.student.display_name ?? '';
                const withdrew = settling?.withdraw ?? false;
                setSettling(null);
                // The row's button is gone: the notice takes the focus.
                announce(
                  t(
                    withdrew
                      ? 'billing.withdrawals.done'
                      : 'billing.withdrawals.settled',
                    {
                      student: name,
                      subject: detail.name,
                      credit: money.money(confirmed.withdrawal.amount),
                      refund: money.money(confirmed.withdrawal.refund_amount),
                      after: money.money(confirmed.account_credit_after),
                    },
                  ),
                  true,
                );
                void refresh();
              }}
              onClose={() => setSettling(null)}
            />
          ) : null}
          <WithdrawDialog
            enrollment={withdrawing}
            groupName={detail.name}
            labels={withdrawLabels}
            onReload={() => {
              setWithdrawing(null);
              void cohort.refetch();
            }}
            onCancel={() => setWithdrawing(null)}
            onConfirm={async (reason) => {
              if (withdrawing) {
                await withdraw(withdrawing, reason);
              }
            }}
          />
        </>
      ) : null}
      {canManage ? (
        <EditCohortDialog
          open={editing}
          cohort={detail}
          roomCapacity={roomCapacity}
          onReload={() => void cohort.refetch()}
          onCancel={() => setEditing(false)}
          onSubmit={async (body) => {
            const updated = await updateCohort(detail.id, body);
            client.setQueryData(cohortKey(detail.id), updated);
            setEditing(false);
            announce(t('courses.cohorts.edit.done'), false);
            await refresh();
          }}
        />
      ) : null}
    </div>
  );
}
