import { useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import {
  createClassSession,
  fetchClassroomOptions,
  fetchOwnerSummary,
  fetchSubjectOptions,
  fetchTutorOptions,
  SCHEDULE_OPTIONS_KEY,
  searchCohortOptions,
  searchGroupOptions,
  searchStudents,
  suggestSeriesTutors,
  suggestSlots,
  suggestTutors,
} from './api';
import {
  fetchClassroomCapacityOptions,
  GROUP_OPTIONS_KEY,
} from '@/features/groups/api';
import { fetchAvailability } from '@/features/tutors/api';
import { MakeupTargetNotice, MakeupUnusableNotice } from './MakeupNotices';
import {
  calendarWeekPath,
  freeTimesBody,
  isSessionId,
  MAKEUP_LINK_CLASS,
  MAKEUP_PARAM,
  makeupTargetProblem,
  makeupValues,
  newSessionValues,
  ownerSessionBody,
  PREFILL_PARAMS,
  prefillValues,
  scheduleBody,
  seriesTutorSuggestionBody,
  sessionPath,
  suggestionBody,
  TUTOR_SUGGESTIONS_LIMIT,
  tutorSuggestionParams,
  type MakeupProblem,
} from './rules';
import { createClassSeries } from '../series/api';
import { seriesCreateBody } from '../series/rules';
import { fetchClassSession } from '../session/api';
import { ScheduleSessionForm } from './ScheduleSessionForm';

// /schedule/new, "Schedule a session" (#48 F1), for schedule.manage holders:
// loads the active subjects, tutors and classrooms, then shows the form.
// The times are in the center zone (the session's business setting).
// Holders of schedule.override can override soft conflicts (#49). After a
// conflict the form shows suggested times (#50). Holders of schedule.assign
// see suggested tutors (#51; HF-05b B: loaded by the form, one-off or
// series, up to 50). Holders of tutor.view see the chosen tutor's weekly
// availability (HF-05b C1), tutor.manage holders a link to edit it, and
// qualification.review holders a link to Tutors when nobody holds the
// subject (B5). "Find free times" (C2) uses the #50 search. "Repeats
// weekly" creates a weekly series (#54b A) and links to its page.
//
// Makeup mode (#436 B), `?makeupOf=<cancelled session>`: the URL parameter is
// the mode's only state. The target is read once, under its own key, and
// never refetched or kept in the cache, so what the form shows and sends is
// one fresh snapshot: a refetch after the save can't swap the success panel
// for "already has a makeup", and the session page's cached copy is never
// used. A target that can't be made up shows one notice instead of the form.
//
// Pre-filled from the calendar (HF-02 C1), `?date&start&end&tutor&room`: read
// once, when the form mounts (rules.prefillValues; invalid values are
// ignored), never in makeup mode. A different pre-fill mounts a new form.
// The built-in General subject (HF-12, #597) is preselected outside makeup
// mode (rules.newSessionValues); the user can pick another.
// After a pre-filled save, the success panel links back to that week.
//
// Group and course sessions (HF-11b, #593): the type selector offers Group
// to schedule.view holders (the groups list) and Course to course.view
// holders (the cohorts list), never in makeup mode. The rooms' capacities
// come from the classrooms list, without holding up the form.

export function ScheduleSessionPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.title'));
  const titleId = useId();
  const timeZone = useCenterTimeZone();
  const canOverride = useCan('schedule.override');
  const canAssign = useCan('schedule.assign');
  const canViewTutors = useCan('tutor.view');
  const canManageTutors = useCan('tutor.manage');
  const canReviewQualifications = useCan('qualification.review');
  const canViewGroups = useCan('schedule.view');
  const canViewCohorts = useCan('course.view');
  const subjects = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
  });
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
  });
  const classrooms = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomOptions(signal),
  });
  const [searchParams, setSearchParams] = useSearchParams();
  const makeupOf = searchParams.get(MAKEUP_PARAM);
  const makeupTarget = useQuery({
    queryKey: ['scheduling', 'create', 'makeup-target', makeupOf],
    queryFn: ({ signal }) => fetchClassSession(makeupOf ?? '', signal),
    enabled: makeupOf !== null && isSessionId(makeupOf),
    staleTime: Infinity,
    gcTime: 0,
    meta: { expectedStatuses: [404] },
  });
  const target = makeupOf === null ? undefined : makeupTarget.data;
  let problem: MakeupProblem | null = null;
  if (makeupOf !== null) {
    if (
      !isSessionId(makeupOf) ||
      (makeupTarget.error instanceof ApiError &&
        makeupTarget.error.status === 404)
    ) {
      problem = 'unknown';
    } else if (target) {
      problem = makeupTargetProblem(target, makeupTarget.dataUpdatedAt);
    }
  }
  const offersOwners = makeupOf === null && (canViewGroups || canViewCohorts);
  const capacities = useQuery({
    queryKey: [...GROUP_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomCapacityOptions(signal),
    enabled: offersOwners,
  });
  const queries = [subjects, tutors, classrooms];
  const failed = queries.find((query) => query.isError);

  let content;
  if (problem) {
    content = <MakeupUnusableNotice problem={problem} />;
  } else if (makeupOf !== null && makeupTarget.isError) {
    content = (
      <ErrorAlert
        error={makeupTarget.error}
        onRetry={() => void makeupTarget.refetch()}
      />
    );
  } else if (failed) {
    content = (
      <ErrorAlert
        error={failed.error}
        onRetry={() => {
          for (const query of queries) {
            if (query.isError) {
              void query.refetch();
            }
          }
        }}
      />
    );
  } else if (
    !subjects.data ||
    !tutors.data ||
    !classrooms.data ||
    (makeupOf !== null && !target)
  ) {
    content = <LoadingState />;
  } else {
    const prefill = target
      ? null
      : prefillValues(searchParams, {
          tutors: tutors.data,
          classrooms: classrooms.data,
        });
    const prefillKey = PREFILL_PARAMS.map(
      (name) => searchParams.get(name) ?? '',
    ).join('|');
    content = (
      <ScheduleSessionForm
        key={target?.id ?? `plain|${prefillKey}`}
        subjects={subjects.data}
        tutors={tutors.data}
        classrooms={classrooms.data}
        searchStudents={searchStudents}
        timeZone={timeZone}
        labelledBy={titleId}
        canOverride={canOverride}
        sessionLink={(sessionId) => (
          <Link
            to={`/schedule/sessions/${encodeURIComponent(sessionId)}`}
            className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('scheduling.success.openSession')}
          </Link>
        )}
        // A series can't carry the link (#436 B5): no "Repeats weekly".
        onSubmitSeries={
          target
            ? undefined
            : (values, recurrence, override) =>
                createClassSeries(
                  seriesCreateBody(values, recurrence, override),
                )
        }
        seriesLink={(seriesId) => (
          <Link
            to={`/schedule/series/${encodeURIComponent(seriesId)}`}
            className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('scheduling.series.success.openSeries')}
          </Link>
        )}
        initialValues={
          target
            ? makeupValues(target)
            : newSessionValues(prefill, subjects.data)
        }
        calendarLink={
          prefill
            ? (date) => (
                <Link
                  to={calendarWeekPath(date, prefill)}
                  className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {t('scheduling.success.viewInCalendar')}
                </Link>
              )
            : undefined
        }
        makeup={
          target
            ? {
                studentName: target.participants[0]?.display_name ?? '',
                subjectName: t('scheduling.success.subject', {
                  subject: target.subject.name,
                  level: target.subject.education_level_name,
                }),
                notice: (
                  <MakeupTargetNotice target={target} timeZone={timeZone} />
                ),
                targetLink: (
                  <Link
                    to={sessionPath(target.id)}
                    className={MAKEUP_LINK_CLASS}
                  >
                    {t('scheduling.makeup.openCancelled')}
                  </Link>
                ),
                takenLink: (sessionId) => (
                  <Link
                    to={sessionPath(sessionId)}
                    className={MAKEUP_LINK_CLASS}
                  >
                    {t('scheduling.makeup.openMakeup')}
                  </Link>
                ),
                // The next session can't inherit the link (B8).
                onScheduleAnother: () => setSearchParams({}, { replace: true }),
              }
            : undefined
        }
        onSubmit={(values, override) =>
          createClassSession(
            scheduleBody(values, timeZone, override, target?.id),
          )
        }
        onSuggest={(values) => suggestSlots(suggestionBody(values, timeZone))}
        onSuggestTutors={
          canAssign
            ? (values, signal) =>
                suggestTutors(
                  {
                    ...tutorSuggestionParams(values, timeZone),
                    limit: TUTOR_SUGGESTIONS_LIMIT,
                  },
                  signal,
                )
            : undefined
        }
        onSuggestSeriesTutors={
          canAssign
            ? (values, recurrence, signal) =>
                suggestSeriesTutors(
                  seriesTutorSuggestionBody(values, recurrence),
                  signal,
                )
            : undefined
        }
        assignSubjectsLink={
          canReviewQualifications ? (
            <Link to="/tutors" className={MAKEUP_LINK_CLASS}>
              {t('scheduling.tutorSuggestions.assignSubjects')}
            </Link>
          ) : undefined
        }
        loadTutorAvailability={canViewTutors ? fetchAvailability : undefined}
        tutorAvailabilityLink={
          canManageTutors
            ? (tutorId) => (
                <Link
                  to={`/tutors/${encodeURIComponent(tutorId)}#availability`}
                  className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {t('scheduling.tutorAvailability.edit')}
                </Link>
              )
            : undefined
        }
        onFindFreeTimes={(values) =>
          suggestSlots(freeTimesBody(values, timeZone))
        }
        owners={
          offersOwners
            ? {
                searchGroups: canViewGroups ? searchGroupOptions : undefined,
                searchCohorts: canViewCohorts ? searchCohortOptions : undefined,
                loadOwner: fetchOwnerSummary,
                onSubmit: (kind, ownerId, values, override) =>
                  createClassSession(
                    ownerSessionBody(kind, ownerId, values, timeZone, override),
                  ),
                classroomCapacities: capacities.data
                  ? Object.fromEntries(
                      capacities.data.map((room) => [room.id, room.capacity]),
                    )
                  : undefined,
                ownerLink: (kind, ownerId) => (
                  <Link
                    to={
                      kind === 'group'
                        ? `/groups/${encodeURIComponent(ownerId)}`
                        : `/courses/cohorts/${encodeURIComponent(ownerId)}`
                    }
                    className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                  >
                    {kind === 'group'
                      ? t('scheduling.create.owner.openGroup')
                      : t('scheduling.create.owner.openCourse')}
                  </Link>
                ),
              }
            : undefined
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('scheduling.title')}
      </h1>
      {content}
    </div>
  );
}
