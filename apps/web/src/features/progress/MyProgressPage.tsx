import { useQuery } from '@tanstack/react-query';
import { useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import { uuidParam } from '@/features/academics/history/urlState';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import {
  fetchFamilyStudents,
  type FamilyStudent,
} from '@/features/my-schedule/api';
import { SELECT_CLASS } from '@/features/students/styles';
import { ProgressLinks } from './ProgressLinks';
import { ProgressView } from './ProgressView';

// /my-progress[?student=] (#322 A2, B4, B6, D1; #350), students and
// guardians: the h1 names the selected entry ("My progress" for the student
// themselves, otherwise "{name}'s progress"; "Progress" while loading, on
// error or with no students), #308's family student picker for 2+ entries
// ("Whose progress": "My progress" for the student themselves, otherwise the
// child's name), kept as ?student= (an unknown or foreign value falls back
// to the first student, with no request for it; one entry has no picker;
// none, a message), the related family links and the shared progress view
// with the lazy "Notes from the tutor". An adult who hasn't shared their
// academic records shows the consent message, with no request. Code-split.

function useFamilyStudent() {
  const [params, setParams] = useSearchParams();
  const students = useQuery({
    queryKey: FAMILY_STUDENTS_KEY,
    queryFn: ({ signal }) => fetchFamilyStudents(signal),
  });
  const items = students.data?.items ?? [];
  const requested = uuidParam(params.get('student'));
  const selected =
    items.find((student) => student.student_id === requested) ?? items[0];
  return {
    students,
    items,
    selected,
    // Another student: the period is kept, the subject (per student) isn't.
    choose: (studentId: string) =>
      setParams((previous) => {
        const next = new URLSearchParams(previous);
        next.set('student', studentId);
        next.delete('subject');
        return next;
      }),
  };
}

export function MyProgressPage() {
  const { t } = useTranslation();
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const { students, items, selected, choose } = useFamilyStudent();
  const title =
    students.isError || !students.data || !selected
      ? t('progress.title')
      : selected.relation === 'self'
        ? t('progress.family.title')
        : t('progress.family.titleOf', { name: selected.display_name });
  useDocumentTitle(title);
  const label = (student: FamilyStudent) => {
    const name =
      student.relation === 'self'
        ? t('progress.family.mine')
        : student.display_name;
    return student.schedule_access === 'consent_required'
      ? t('mySchedule.whose.consentRequired', { name })
      : name;
  };

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
        {t('progress.family.noStudents')}
      </p>
    );
  } else {
    content = (
      <>
        {items.length > 1 ? (
          <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={`${id}-whose`} className="text-sm font-medium">
              {t('progress.family.whose')}
            </label>
            <select
              id={`${id}-whose`}
              className={`${SELECT_CLASS} w-full max-w-sm`}
              value={selected.student_id}
              onChange={(event) => choose(event.target.value)}
            >
              {items.map((student) => (
                <option key={student.student_id} value={student.student_id}>
                  {label(student)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {selected.schedule_access === 'consent_required' ? (
          <ConsentRequired
            name={selected.display_name}
            title={t('academics.history.consentTitle')}
          />
        ) : (
          <ProgressView
            key={selected.student_id}
            studentId={selected.student_id}
            audience="family"
            studentName={selected.display_name}
            headingRef={heading}
            links={
              <ProgressLinks
                studentId={selected.student_id}
                audience="family"
              />
            }
          />
        )}
      </>
    );
  }

  return (
    <div className="flex w-full max-w-6xl min-w-0 flex-col gap-6">
      <h1
        ref={heading}
        tabIndex={-1}
        className="text-2xl font-semibold outline-none"
      >
        {title}
      </h1>
      {content}
    </div>
  );
}

export default MyProgressPage;
