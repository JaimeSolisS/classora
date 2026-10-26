import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import type { FamilyStudent } from '@/features/my-schedule/api';
import { SELECT_CLASS } from '@/features/students/styles';
import { myReportPath } from './api';
import { usePickerLabel, useFamilyStudent } from './family';
import { useApprovedList } from './queries';
import { ApprovedCard, CardList } from './ReportCards';

// /my-progress-reports[?student=] (#315 F4, F6), students and guardians
// (MY_PROGRESS_REPORTS_ACCESS): the h1 "Tutor reports" and #308's student
// picker ("Whose reports": "My reports" for the student themselves,
// otherwise the child's name), kept as ?student=. An adult child who hasn't
// shared their academic records (or a 403 student_consent_required) shows
// the consent message; a 404 shows "These reports aren't available."
// inline, so the picker stays usable. The approved reports as cards
// linking to /my-progress-reports/:id?student=, with "Load more". Only
// approved reports exist here: no status, no reason, no action.

function FamilyReports({ student }: { student: FamilyStudent }) {
  const { t } = useTranslation();
  const { query, state } = useApprovedList(student.student_id);
  if (
    query.error instanceof ApiError &&
    query.error.code === 'student_consent_required'
  ) {
    return (
      <ConsentRequired
        name={student.display_name}
        title={t('academics.history.consentTitle')}
      />
    );
  }
  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <p role="status" className="text-sm">
        {t('progressReports.family.listMissing')}
      </p>
    );
  }
  return (
    <CardList
      state={state}
      empty={<p className="text-sm">{t('progressReports.family.empty')}</p>}
      render={(report) => (
        <ApprovedCard
          key={report.id}
          report={report}
          to={myReportPath(report.id, student.student_id)}
        />
      )}
    />
  );
}

export function MyReportsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('progressReports.family.title'));
  const id = useId();
  const label = usePickerLabel();
  const { students, items, selected, choose } = useFamilyStudent();

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
              {t('progressReports.family.whose')}
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
        ) : (
          <p className="text-sm">
            <span className="font-medium">
              {t('progressReports.family.whose')}
            </span>{' '}
            {label(selected)}
          </p>
        )}
        {selected.schedule_access === 'consent_required' ? (
          <ConsentRequired
            name={selected.display_name}
            title={t('academics.history.consentTitle')}
          />
        ) : (
          <FamilyReports key={selected.student_id} student={selected} />
        )}
      </>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">
        {t('progressReports.family.title')}
      </h1>
      {content}
    </div>
  );
}

export default MyReportsPage;
