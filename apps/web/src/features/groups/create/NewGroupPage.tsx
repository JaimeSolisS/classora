import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { CALENDAR_KEY } from '@/features/calendar/api';
import {
  fetchSubjectOptions,
  fetchTutorOptions,
  SCHEDULE_OPTIONS_KEY,
  searchStudents,
} from '@/features/scheduling/create/api';
import {
  createGroup,
  fetchClassroomCapacityOptions,
  GROUP_OPTIONS_KEY,
  GROUPS_KEY,
  groupKey,
} from '../api';
import { groupCreateBody } from '../rules';
import { GroupCreateForm } from './GroupCreateForm';

// /groups/new (#234 E), for schedule.manage holders: loads the active
// subjects, tutors and classrooms (with their capacity), then shows the
// form. After a 201 the app goes to the new group's page with "Group
// created: N upcoming sessions" (E3 choice). Holders of schedule.override
// can override the soft conflicts POST /groups accepts.

export function NewGroupPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('groups.create.title'));
  const titleId = useId();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const canOverride = useCan('schedule.override');
  const subjects = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
  });
  const tutors = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'tutors'],
    queryFn: ({ signal }) => fetchTutorOptions(signal),
  });
  const classrooms = useQuery({
    queryKey: [...GROUP_OPTIONS_KEY, 'classrooms'],
    queryFn: ({ signal }) => fetchClassroomCapacityOptions(signal),
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
      <GroupCreateForm
        subjects={subjects.data}
        tutors={tutors.data}
        classrooms={classrooms.data}
        searchStudents={searchStudents}
        timeZone={timeZone}
        canOverride={canOverride}
        labelledBy={titleId}
        onSubmit={(values, recurrence, override) =>
          createGroup(groupCreateBody(values, recurrence, override))
        }
        onCreated={(group) => {
          // GroupCreated is a GroupDetail plus `warnings` (always empty).
          client.setQueryData(groupKey(group.id), group);
          void client.invalidateQueries({ queryKey: [...GROUPS_KEY, 'list'] });
          void client.invalidateQueries({ queryKey: CALENDAR_KEY });
          void navigate(`/groups/${encodeURIComponent(group.id)}`, {
            state: {
              notice: t('groups.create.done', {
                count: group.next_occurrences.length,
              }),
            },
          });
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('groups.create.title')}
      </h1>
      {content}
    </div>
  );
}
