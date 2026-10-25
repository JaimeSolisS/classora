import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { toLocalDate, todayInZone } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { invalidateAcademicLists } from '../history/api';
import { SMALL_LINK_CLASS } from '../styles';
import { fetchEvaluationTemplates, templatesKey } from '../templates/api';
import {
  createEvaluation,
  deleteEvaluation,
  evaluationContextKey,
  evaluationKey,
  evaluationPath,
  fetchEvaluation,
  fetchEvaluationContext,
  finalizeEvaluation,
  newEvaluationPath,
  patchEvaluation,
  studentHistoryPath,
  voidEvaluation,
  type Evaluation,
} from './api';
import { EvaluationDetail } from './EvaluationDetail';
import { NewEvaluationForm, type TemplatesState } from './NewEvaluationForm';

// /students/:studentId/evaluations/new[?sessionId=] (#308 C1, tutors) and
// /evaluations/:evaluationId (#308 C4, tutors and evaluation.manage
// holders), code-split by the router. The API's record policies decide:
// an unknown or hidden student or evaluation shows the not-found page.
//
// New: the student from GET /students/{id}; with ?sessionId= the class's
// evaluation context (its current tutor only). A 404, a student who isn't
// in the class or a class that can't be evaluated yet shows "This class
// can't be evaluated." with the link to the form without it, and nothing is
// sent. After the create, the page goes to the evaluation (replacing the
// history entry), which says "Draft saved.".
//
// One evaluation: GET /evaluations/{id}; each write stores the returned
// view and invalidates the history and the working lists. A deleted draft
// goes to the student's Evaluations tab.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children}
    </div>
  );
}

export function NewEvaluationPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('academics.evaluations.newTitle'));
  const { studentId = '' } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const rawSession = searchParams.get('sessionId');
  const sessionId = rawSession && UUID.test(rawSession) ? rawSession : null;
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
  });
  const context = useQuery({
    queryKey: evaluationContextKey(sessionId ?? ''),
    queryFn: ({ signal }) => fetchEvaluationContext(sessionId ?? '', signal),
    enabled: sessionId !== null,
    retry: false,
  });
  const contextMissing =
    context.error instanceof ApiError && context.error.status === 404;
  const sessionProblem =
    (rawSession !== null && sessionId === null) ||
    contextMissing ||
    (context.data !== undefined &&
      (!context.data.evaluable ||
        !context.data.participants.some(
          (participant) => participant.student_id === studentId,
        )));
  const contextReady = sessionId === null || context.data !== undefined;
  const filters = {
    status: 'active' as const,
    kind: null,
    subject: context.data?.subject.id ?? null,
    q: '',
  };
  const templates = useQuery({
    queryKey: templatesKey(filters),
    queryFn: ({ signal }) => fetchEvaluationTemplates(filters, signal),
    enabled: contextReady && !sessionProblem,
  });
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: sessionId === null,
  });
  const withoutSession = newEvaluationPath(studentId);

  if (student.error instanceof ApiError && student.error.status === 404) {
    return <NotFoundPage />;
  }
  const title = t('academics.evaluations.newTitle');
  if (student.isError) {
    return (
      <Shell title={title}>
        <ErrorAlert
          error={student.error}
          onRetry={() => void student.refetch()}
        />
      </Shell>
    );
  }
  if (sessionProblem) {
    return (
      <Shell title={title}>
        <div
          role="alert"
          className="flex flex-col items-start gap-2 rounded-lg border border-destructive p-3 text-sm"
        >
          <p className="font-medium text-destructive">
            {t('academics.evaluations.notEvaluable')}
          </p>
          <Link to={withoutSession} className={SMALL_LINK_CLASS}>
            {t('academics.evaluations.withoutSession')}
          </Link>
        </div>
      </Shell>
    );
  }
  if (context.isError) {
    return (
      <Shell title={title}>
        <ErrorAlert
          error={context.error}
          onRetry={() => void context.refetch()}
        />
      </Shell>
    );
  }
  if (!student.data || !contextReady) {
    return (
      <Shell title={title}>
        <LoadingState rows={4} />
      </Shell>
    );
  }

  const templatesState: TemplatesState = templates.data
    ? { status: 'ready', items: templates.data }
    : templates.isError
      ? {
          status: 'error',
          error: templates.error,
          onRetry: () => void templates.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link
        to={studentHistoryPath(studentId)}
        className={`${SMALL_LINK_CLASS} self-start`}
      >
        {t('academics.evaluations.backToHistory')}
      </Link>
      <NewEvaluationForm
        student={{ id: studentId, display_name: fullName(student.data) }}
        context={context.data ?? null}
        templates={templatesState}
        subjects={subjects.data ?? []}
        today={todayInZone(timeZone)}
        sessionDate={
          context.data ? toLocalDate(context.data.starts_at, timeZone) : null
        }
        timeZone={timeZone}
        withoutSession={withoutSession}
        onReloadTemplates={() => void templates.refetch()}
        onCreate={async (body) => {
          const created = await createEvaluation(body);
          client.setQueryData(evaluationKey(created.id), created);
          void invalidateAcademicLists(client);
          void navigate(evaluationPath(created.id), {
            replace: true,
            state: { notice: 'draftSaved' },
          });
        }}
      />
    </div>
  );
}

export function EvaluationPage() {
  const { t } = useTranslation();
  const { evaluationId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const [generation, setGeneration] = useState(0);
  const [missing, setMissing] = useState(false);
  const key = evaluationKey(evaluationId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchEvaluation(evaluationId, signal),
    retry: false,
  });
  useDocumentTitle(
    detail.data
      ? t('academics.evaluations.title', {
          student: detail.data.student.display_name,
        })
      : t('academics.evaluations.pageTitle'),
  );
  const arrived = (location.state as { notice?: string } | null)?.notice;

  if (
    missing ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return <NotFoundPage />;
  }
  if (detail.isError) {
    return (
      <Shell title={t('academics.evaluations.pageTitle')}>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell title={t('academics.evaluations.pageTitle')}>
        <LoadingState rows={6} />
      </Shell>
    );
  }

  const current = () => client.getQueryData<Evaluation>(key) ?? detail.data;

  /** A write: stores the view, refreshes the lists; a 404 shows the
   * not-found page. */
  async function write(call: () => Promise<Evaluation>): Promise<Evaluation> {
    try {
      const stored = await call();
      client.setQueryData(key, stored);
      void invalidateAcademicLists(client);
      return stored;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setMissing(true);
      }
      throw caught;
    }
  }

  const studentId = detail.data.student.id;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link
        to={studentHistoryPath(studentId)}
        className={`${SMALL_LINK_CLASS} self-start`}
      >
        {t('academics.evaluations.backToHistory')}
      </Link>
      <EvaluationDetail
        key={`${evaluationId}-${generation}`}
        evaluation={detail.data}
        today={todayInZone(timeZone)}
        initialNotice={
          arrived === 'draftSaved' && generation === 0
            ? t('academics.evaluations.notices.draftSaved')
            : null
        }
        actions={{
          onSave: (body) => write(() => patchEvaluation(evaluationId, body)),
          onFinalize: () => write(() => finalizeEvaluation(current())),
          onVoid: (reason) => write(() => voidEvaluation(current(), reason)),
          onDelete: async () => {
            try {
              await deleteEvaluation(current());
            } catch (caught) {
              if (caught instanceof ApiError && caught.status === 404) {
                setMissing(true);
              }
              throw caught;
            }
            client.removeQueries({ queryKey: key });
            void invalidateAcademicLists(client);
            void navigate(studentHistoryPath(studentId, 'evaluations'), {
              state: { notice: 'evaluationDeleted' },
            });
          },
          onReload: () => {
            void detail.refetch().then(() => setGeneration((n) => n + 1));
          },
        }}
      />
    </div>
  );
}
