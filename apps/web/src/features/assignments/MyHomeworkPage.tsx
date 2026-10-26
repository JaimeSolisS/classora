import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import { SELECT_CLASS } from '@/features/students/styles';
import { myHomeworkItemPath } from './api';
import { familyLabel, useFamilyStudent } from './family';
import { StudentAssignmentsList } from './StudentAssignmentsList';

// /my-homework[?student=][&state=] (#313 E1-E3, E8), students and
// guardians (MY_HOMEWORK_ACCESS): the h1 "Homework" and, from GET
// /schedule/family-students, the picker of #308 ("Whose homework": "My
// homework" for the student themselves, otherwise the child's name), kept
// as ?student=; with only one entry, plain text instead. An unknown or
// foreign value falls back to the first student, with no request for it.
// An adult child who hasn't shared their academic records (or a 403
// student_consent_required) shows the consent message; a 404 shows "This
// homework list isn't available." inline, so the picker stays usable. The
// rows link to /my-homework/:id?student=.

export function MyHomeworkPage() {
  const { t } = useTranslation();
  const title = t('assignments.student.title');
  useDocumentTitle(title);
  const id = useId();
  const [, setSearchParams] = useSearchParams();
  const { students, items, selected } = useFamilyStudent();

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
              {t('assignments.family.whose')}
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
                  {familyLabel(student, t)}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <p className="text-sm">
            <span className="font-medium">{t('assignments.family.whose')}</span>{' '}
            {familyLabel(selected, t)}
          </p>
        )}
        {selected.schedule_access === 'consent_required' ? (
          <ConsentRequired
            name={selected.display_name}
            title={t('academics.history.consentTitle')}
          />
        ) : (
          <StudentAssignmentsList
            key={selected.student_id}
            studentId={selected.student_id}
            studentName={selected.display_name}
            linkTo={(assignmentId) =>
              myHomeworkItemPath(assignmentId, selected.student_id)
            }
            emptyText={t('assignments.student.empty')}
          />
        )}
      </>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {content}
    </div>
  );
}

export default MyHomeworkPage;
