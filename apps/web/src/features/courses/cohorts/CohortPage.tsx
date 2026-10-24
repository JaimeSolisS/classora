import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { CALENDAR_KEY } from '@/features/calendar/api';
import {
  fetchClassroomCapacityOptions,
  GROUP_OPTIONS_KEY,
} from '@/features/groups/api';
import {
  fetchTutorOptions,
  SCHEDULE_OPTIONS_KEY,
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
import { NotFoundPage } from '@/pages/NotFoundPage';
import { CoursesTabs } from '../CoursesTabs';
import {
  cohortKey,
  COHORTS_KEY,
  cohortSessionsKey,
  fetchCohort,
  fetchCohortSessions,
  updateCohort,
} from './api';
import {
  CohortSessionsSection,
  type CohortSessionsState,
} from './CohortSessionsSection';
import { CohortView } from './CohortView';
import { EditCohortDialog } from './EditCohortDialog';
import {
  canOfferSeries,
  offerFromDate,
  roomLimited,
  sessionsWindow,
  shiftWindow,
} from './rules';

// /courses/cohorts/:cohortId (#243 D), for course.view holders: the
// details, the tutor assignment, the next sessions, the sessions of a
// 4-week window and the syllabus (the cohort's snapshot). schedule.manage
// holders edit the name and maximum; schedule.assign holders (Coordinators
// too) offer the series to a tutor while it has none, from today (or its
// start) with the #51 suggestions for its next session. An unknown id (404)
// is the shared not-found page. Every write refreshes the cohort (and the
// list and the calendar) and is announced at the top. No enrollment here
// (#245 adds a Students section to the composed page).

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
  const location = useLocation();
  const timeZone = useCenterTimeZone();
  const { formatLocalDate } = useFormatters();
  const canManage = useCan('schedule.manage');
  const canAssign = useCan('schedule.assign');
  const canViewSchedule = useCan('schedule.view');
  const canViewCourses = useCan('course.view');
  const client = useQueryClient();
  const [notice, setNotice] = useState<string | null>(
    (location.state as PageNotice | null)?.notice ?? null,
  );
  const [noticeFocus, setNoticeFocus] = useState(notice !== null);
  const [editing, setEditing] = useState(false);
  const [windowStart, setWindowStart] = useState<string>(() =>
    todayInZone(timeZone),
  );

  const cohort = useQuery({
    queryKey: cohortKey(cohortId),
    queryFn: ({ signal }) => fetchCohort(cohortId, signal),
  });
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
          onPrevious={() => setWindowStart((d) => shiftWindow(d, -1))}
          onNext={() => setWindowStart((d) => shiftWindow(d, 1))}
        />
      </CohortView>
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
