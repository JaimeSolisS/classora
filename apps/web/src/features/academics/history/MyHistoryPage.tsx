import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import {
  fetchFamilyStudents,
  type FamilyStudent,
} from '@/features/my-schedule/api';
import { SELECT_CLASS } from '@/features/students/styles';
import { AcademicHistory } from './AcademicHistory';
import { FAMILY_STUDENTS_KEY } from './api';
import { uuidParam } from './urlState';

// /my-history (#308 E8), students and guardians (MY_HISTORY_ACCESS): the
// h1 "Academic history" and, from GET /schedule/family-students, the
// student picker of My schedule without "My teaching" ("Whose history":
// "My history" for the student themselves, otherwise the child's name),
// kept as ?student=. An unknown or foreign value falls back to the first
// student, with no request for it. An adult child who hasn't shared their
// academic records shows the consent message and nothing is requested. No
// tabs, no "Details", no "New evaluation". Code-split by the router.

function pickerLabel(student: FamilyStudent, t: TFunction<'common'>): string {
  const name =
    student.relation === 'self'
      ? t('academics.history.mine')
      : student.display_name;
  return student.schedule_access === 'consent_required'
    ? t('mySchedule.whose.consentRequired', { name })
    : name;
}

export function MyHistoryPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('academics.history.title'));
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
              {t('academics.history.whose')}
            </label>
            <select
              id={`${id}-whose`}
              className={`${SELECT_CLASS} w-full max-w-sm`}
              value={selected.student_id}
              onChange={(event) => {
                const params = new URLSearchParams();
                for (const name of ['group', 'view']) {
                  const value = searchParams.get(name);
                  if (value) params.set(name, value);
                }
                params.set('student', event.target.value);
                setSearchParams(params);
              }}
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
            <span className="font-medium">{t('academics.history.whose')}</span>{' '}
            {pickerLabel(selected, t)}
          </p>
        )}
        {selected.schedule_access === 'consent_required' ? (
          <ConsentRequired
            name={selected.display_name}
            title={t('academics.history.consentTitle')}
          />
        ) : (
          <AcademicHistory
            key={selected.student_id}
            studentId={selected.student_id}
            audience="family"
            studentName={selected.display_name}
          />
        )}
      </>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t('academics.history.title')}</h1>
      {content}
    </div>
  );
}

export default MyHistoryPage;
