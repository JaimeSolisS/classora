import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { DEFAULT_CENTER_TIME_ZONE, todayInZone } from '@/lib/time';
import {
  ACTIVE_LEVELS_QUERY_KEY,
  createStudent,
  fetchActiveLevels,
  fetchStudent,
  STUDENTS_LIST_QUERY_KEY,
  studentDetailKey,
  updateStudent,
  type StudentDetail,
} from './api';
import {
  createBody,
  EMPTY_STUDENT,
  fullName,
  updateBody,
  valuesOf,
} from './rules';
import { StudentForm } from './StudentForm';
import { TEXT_LINK_CLASS } from './styles';

// /students/new and /students/:studentId/edit (#33 J1, J2), both for
// student.manage holders. Create sends POST and goes to the detail; edit
// sends `version` plus the changed fields and goes back to the detail. After
// a save the detail cache holds the response, the list queries are
// invalidated, and the detail page says "Changes saved." (polite status).

/** Navigation state the detail page reads to announce a save. */
export interface SavedState {
  saved: true;
}

function useActiveLevels() {
  return useQuery({
    queryKey: ACTIVE_LEVELS_QUERY_KEY,
    queryFn: ({ signal }) => fetchActiveLevels(signal),
  });
}

function useStore() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return (student: StudentDetail) => {
    queryClient.setQueryData(studentDetailKey(student.id), student);
    void queryClient.invalidateQueries({ queryKey: STUDENTS_LIST_QUERY_KEY });
    const state: SavedState = { saved: true };
    void navigate(`/students/${student.id}`, { state });
  };
}

export function NewStudentPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('students.add'));
  const titleId = useId();
  const levels = useActiveLevels();
  const store = useStore();
  const today = todayInZone(DEFAULT_CENTER_TIME_ZONE);

  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to="/students" className={TEXT_LINK_CLASS}>
          {t('students.back')}
        </Link>
      </p>
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('students.add')}
      </h1>
      <StudentForm
        defaultValues={EMPTY_STUDENT}
        levels={levels.data?.items ?? []}
        today={today}
        cancelTo="/students"
        labelledBy={titleId}
        onSubmit={async (values) => {
          store(await createStudent(createBody(values)));
        }}
      />
    </div>
  );
}

export function EditStudentPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const titleId = useId();
  const key = studentDetailKey(studentId);
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
  });
  const levels = useActiveLevels();
  const store = useStore();
  useDocumentTitle(t('students.edit'));
  const today = todayInZone(DEFAULT_CENTER_TIME_ZONE);

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('students.notFound') : t('students.edit')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        <p>
          <Link to="/students" className={TEXT_LINK_CLASS}>
            {t('students.back')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  const student = detail.data;
  return (
    <div className="flex flex-col gap-6">
      <p>
        <Link to={`/students/${student.id}`} className={TEXT_LINK_CLASS}>
          {t('students.backToStudent')}
        </Link>
      </p>
      <h1 id={titleId} className="text-2xl font-semibold break-words">
        {t('students.editNamed', { name: fullName(student) })}
      </h1>
      {student.personal ? (
        <StudentForm
          key={student.version}
          defaultValues={valuesOf(student)}
          levels={levels.data?.items ?? []}
          currentLevel={student.education_level}
          today={today}
          cancelTo={`/students/${student.id}`}
          labelledBy={titleId}
          onSubmit={async (values) => {
            const current =
              queryClient.getQueryData<StudentDetail>(key) ?? student;
            store(await updateStudent(student.id, updateBody(current, values)));
          }}
          onReload={async () => {
            await detail.refetch();
          }}
        />
      ) : (
        <ErrorAlert
          error={
            new ApiError({
              code: 'permission_denied',
              status: 403,
              requestId: undefined,
            })
          }
        />
      )}
    </div>
  );
}
