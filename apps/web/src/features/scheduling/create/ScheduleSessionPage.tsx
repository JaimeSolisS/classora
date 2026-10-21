import { useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  createClassSession,
  fetchClassroomOptions,
  fetchSubjectOptions,
  fetchTutorOptions,
  SCHEDULE_OPTIONS_KEY,
  searchStudents,
} from './api';
import { scheduleBody } from './rules';
import { ScheduleSessionForm } from './ScheduleSessionForm';

// /schedule/new, "Schedule a session" (#48 F1), for schedule.manage holders:
// loads the active subjects, tutors and classrooms, then shows the form.
// The times are in the center zone (the session's business setting).

export function ScheduleSessionPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('scheduling.title'));
  const titleId = useId();
  const timeZone = useCenterTimeZone();
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
  const queries = [subjects, tutors, classrooms];
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
  } else if (!subjects.data || !tutors.data || !classrooms.data) {
    content = <LoadingState />;
  } else {
    content = (
      <ScheduleSessionForm
        subjects={subjects.data}
        tutors={tutors.data}
        classrooms={classrooms.data}
        searchStudents={searchStudents}
        timeZone={timeZone}
        labelledBy={titleId}
        onSubmit={(values) =>
          createClassSession(scheduleBody(values, timeZone))
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
