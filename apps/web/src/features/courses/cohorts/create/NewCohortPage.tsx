import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
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
} from '@/features/scheduling/create/api';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import {
  cohortKey,
  COHORTS_KEY,
  createCohort,
  fetchTemplateOptions,
  templateOptionsKey,
} from '../api';
import { cohortCreateBody } from '../rules';
import { CohortCreateForm } from './CohortCreateForm';

// /courses/cohorts/new (#243 C), for schedule.manage holders: loads the
// active course templates, the tutors and the classrooms (with their
// capacity), then shows the form. After a 201 the app goes to the new
// cohort's page with "Cohort created". Holders of schedule.override can
// override the soft conflicts of single occurrences.

export function NewCohortPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('courses.cohorts.create.title'));
  const titleId = useId();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const canOverride = useCan('schedule.override');
  const templates = useQuery({
    queryKey: templateOptionsKey('active'),
    queryFn: ({ signal }) => fetchTemplateOptions('active', signal),
  });
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
  });
  const classrooms = useQuery({
    queryKey: [...GROUP_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomCapacityOptions(signal),
  });
  const queries = [templates, tutors, classrooms];
  const failed = queries.find((query) => query.isError);

  let content;
  if (failed) {
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
  } else if (!templates.data || !tutors.data || !classrooms.data) {
    content = <LoadingState />;
  } else {
    content = (
      <CohortCreateForm
        templates={templates.data}
        tutors={tutors.data}
        classrooms={classrooms.data}
        timeZone={timeZone}
        canOverride={canOverride}
        labelledBy={titleId}
        onSubmit={(values, recurrence, override) =>
          createCohort(cohortCreateBody(values, recurrence, timeZone, override))
        }
        onCreated={(cohort) => {
          // CohortCreated is a CohortDetail plus `warnings` (always empty).
          client.setQueryData(cohortKey(cohort.id), cohort);
          void client.invalidateQueries({ queryKey: [...COHORTS_KEY, 'list'] });
          void client.invalidateQueries({ queryKey: CALENDAR_KEY });
          void navigate(`/courses/cohorts/${encodeURIComponent(cohort.id)}`, {
            state: { notice: t('courses.cohorts.create.done') },
          });
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Link
        to="/courses/cohorts"
        className={`${TEXT_LINK_CLASS} self-start text-sm`}
      >
        {t('courses.cohorts.back')}
      </Link>
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('courses.cohorts.create.title')}
      </h1>
      {content}
    </div>
  );
}
