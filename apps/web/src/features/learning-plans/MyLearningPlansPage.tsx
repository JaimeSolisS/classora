import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import { uuidParam } from '@/features/academics/history/urlState';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import {
  fetchFamilyStudents,
  type FamilyStudent,
} from '@/features/my-schedule/api';
import { SELECT_CLASS } from '@/features/students/styles';
import { fetchPlanList, myPlanPath, planListKey } from './api';
import { PlanList, type PlanListState } from './PlanList';

// /my-learning-plans[?student=] (#310 E1, E2, E4), students and guardians
// (MY_LEARNING_PLANS_ACCESS): the h1 "Learning plans" and, from GET
// /schedule/family-students, the picker of My schedule without "My
// teaching" ("Whose plans": "My plans" for the student themselves,
// otherwise the child's name), kept as ?student=. An unknown or foreign
// value falls back to the first student, with no request for it. An adult
// child who hasn't shared their academic records (or a 403
// student_consent_required) shows the consent message; a 404 shows "These
// learning plans aren't available." inline, so the picker stays usable.
// The rows link to /my-learning-plans/:id; no "New plan", no actions.

function pickerLabel(student: FamilyStudent, t: TFunction<'common'>): string {
  const name =
    student.relation === 'self'
      ? t('learningPlans.family.mine')
      : student.display_name;
  return student.schedule_access === 'consent_required'
    ? t('mySchedule.whose.consentRequired', { name })
    : name;
}

function FamilyPlans({ student }: { student: FamilyStudent }) {
  const { t } = useTranslation();
  const list = useQuery({
    queryKey: planListKey(student.student_id),
    queryFn: ({ signal }) => fetchPlanList(student.student_id, signal),
    retry: false,
  });
  if (
    list.error instanceof ApiError &&
    list.error.code === 'student_consent_required'
  ) {
    return (
      <ConsentRequired
        name={student.display_name}
        title={t('academics.history.consentTitle')}
      />
    );
  }
  if (list.error instanceof ApiError && list.error.status === 404) {
    return (
      <p role="status" className="text-sm">
        {t('learningPlans.family.listMissing')}
      </p>
    );
  }
  const state: PlanListState = list.data
    ? {
        status: 'ready',
        items: list.data.items,
        truncated: list.data.truncated,
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };
  return (
    <PlanList
      state={state}
      linkTo={myPlanPath}
      emptyText={t('learningPlans.family.empty')}
    />
  );
}

export function MyLearningPlansPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('learningPlans.title'));
  const id = useId();
  const [searchParams, setSearchParams] = useSearchParams();
  const students = useQuery({
    queryKey: FAMILY_STUDENTS_KEY,
    queryFn: ({ signal }) => fetchFamilyStudents(signal),
  });
  const items = students.data?.items ?? [];
  const requested = uuidParam(searchParams.get('student'));
  const selected =
    items.find((student) => student.student_id === requested) ?? items[0];

  let content;
  if (students.isError) {
    content = (
      <ErrorAlert
        error={students.error}
        onRetry={() => void students.refetch()}
      />
    );
  } else if (!students.data) {
    content = <LoadingState rows={4} />;
  } else if (!selected) {
    content = (
      <p role="status" className="text-sm">
        {t('academics.history.noStudents')}
      </p>
    );
  } else {
    content = (
      <>
        {items.length > 1 ? (
          <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={`${id}-whose`} className="text-sm font-medium">
              {t('learningPlans.family.whose')}
            </label>
            <select
              id={`${id}-whose`}
              className={`${SELECT_CLASS} w-full max-w-sm`}
              value={selected.student_id}
              onChange={(event) =>
                setSearchParams(
                  new URLSearchParams({ student: event.target.value }),
                )
              }
            >
              {items.map((student) => (
                <option key={student.student_id} value={student.student_id}>
                  {pickerLabel(student, t)}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <p className="text-sm">
            <span className="font-medium">
              {t('learningPlans.family.whose')}
            </span>{' '}
            {pickerLabel(selected, t)}
          </p>
        )}
        {selected.schedule_access === 'consent_required' ? (
          <ConsentRequired
            name={selected.display_name}
            title={t('academics.history.consentTitle')}
          />
        ) : (
          <FamilyPlans key={selected.student_id} student={selected} />
        )}
      </>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t('learningPlans.title')}</h1>
      {content}
    </div>
  );
}

export default MyLearningPlansPage;
