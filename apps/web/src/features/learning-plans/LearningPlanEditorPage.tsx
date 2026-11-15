import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { todayInZone } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly } from './access';
import {
  createPlan,
  fetchPlan,
  fetchPlanList,
  invalidatePlanLists,
  myPlanPath,
  myPlansPath,
  planKey,
  planListKey,
  planPath,
  replacePlan,
  studentPlansPath,
} from './api';
import { LearningPlanEditor, type SubjectChoice } from './LearningPlanEditor';
import { activeSubjectIds, createBody, replaceBody } from './rules';

// /students/:studentId/learning-plans/new (#310 D1) and
// /learning-plans/:planId/edit (#310 D7), for learning_plan.manage holders
// and tutors (the API decides). Code-split by the router.
//
// New: the student (GET /students/{id}; 404 → not-found page) and the
// student's plan list for its create hints (AM1): a tutor is offered
// `creatable_subjects` (labelled with their level from GET /subjects when
// it answers), a holder (`null`) the active subjects of GET /subjects; with `can_create: false` the page says so and shows no form.
// After the create the page goes (replace) to the plan, which says "Draft
// saved. Activate it to share it with the family."
//
// Edit: the plan (404 → not-found page); without `can_edit`, "This plan
// can't be edited." and a link to the plan, no form. After the save the
// page goes (replace) to the plan, which says "Plan saved.".
//
// Reload (after a conflict or a changed state) refetches, then remounts
// the editor, which focuses its heading. A student or guardian goes to the
// family pages.

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children}
    </div>
  );
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className={`${SMALL_LINK_CLASS} self-start`}>
      {label}
    </Link>
  );
}

export function NewLearningPlanPage() {
  const { t } = useTranslation();
  const title = t('learningPlans.editor.newTitle');
  useDocumentTitle(title);
  const { studentId = '' } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const familyOnly = useFamilyOnly();
  const [generation, setGeneration] = useState(0);
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const list = useQuery({
    queryKey: planListKey(studentId),
    queryFn: ({ signal }) => fetchPlanList(studentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const anySubject = list.data?.creatable_subjects === null;
  // Holders choose among the active subjects; a tutor's creatable subjects
  // carry names only, so the same read gives their levels (two subjects of
  // one name, such as Matemáticas in two levels, stay distinguishable).
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: list.data?.can_create === true && !familyOnly,
  });

  if (familyOnly) {
    return <Navigate replace to={myPlansPath(studentId)} />;
  }
  const missing = (error: unknown) =>
    error instanceof ApiError && error.status === 404;
  if (missing(student.error) || missing(list.error)) {
    return <NotFoundPage />;
  }
  const failed = student.isError
    ? { error: student.error, retry: () => void student.refetch() }
    : list.isError
      ? { error: list.error, retry: () => void list.refetch() }
      : anySubject && subjects.isError
        ? { error: subjects.error, retry: () => void subjects.refetch() }
        : null;
  if (failed) {
    return (
      <Shell title={title}>
        <ErrorAlert error={failed.error} onRetry={failed.retry} />
      </Shell>
    );
  }
  if (
    !student.data ||
    !list.data ||
    (list.data.can_create && subjects.isPending)
  ) {
    return (
      <Shell title={title}>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  const name = fullName(student.data);
  const back = (
    <BackLink
      to={studentPlansPath(studentId)}
      label={t('learningPlans.backToPlans')}
    />
  );
  if (!list.data.can_create) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {back}
        <Shell title={title}>
          <p role="status" className="text-sm">
            {t('learningPlans.editor.cannotCreate')}
          </p>
        </Shell>
      </div>
    );
  }
  const levels = new Map(
    (subjects.data ?? []).map((subject) => [subject.id, subject.levelName]),
  );
  const label = (subject: { id: string; name: string }) => {
    const level = levels.get(subject.id);
    return level
      ? t('academics.subjectOption', { subject: subject.name, level })
      : subject.name;
  };
  const choices: SubjectChoice[] = (
    list.data.creatable_subjects ??
    subjects.data ??
    []
  ).map((subject) => ({ id: subject.id, label: label(subject) }));

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {back}
      <LearningPlanEditor
        key={generation}
        plan={null}
        today={todayInZone(timeZone)}
        studentName={name}
        subjects={choices}
        activeSubjectIds={activeSubjectIds(list.data.items)}
        focusHeadingOnMount={generation > 0}
        onReload={() => {
          void Promise.all([list.refetch(), subjects.refetch()]).then(() =>
            setGeneration((n) => n + 1),
          );
        }}
        onSave={async (values) => {
          const created = await createPlan(studentId, createBody(values));
          client.setQueryData(planKey(created.id), created);
          void invalidatePlanLists(client);
          void navigate(planPath(created.id), {
            replace: true,
            state: { notice: 'draftSaved' },
          });
        }}
      />
    </div>
  );
}

export function EditLearningPlanPage() {
  const { t } = useTranslation();
  const { planId = '' } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const familyOnly = useFamilyOnly();
  const [generation, setGeneration] = useState(0);
  const [missing, setMissing] = useState(false);
  const key = planKey(planId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchPlan(planId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const studentId = detail.data?.student_id ?? '';
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled: studentId !== '' && !familyOnly,
  });
  const title = t('learningPlans.editor.editTitle');
  useDocumentTitle(title);

  if (familyOnly) {
    return <Navigate replace to={myPlanPath(planId)} />;
  }
  if (
    missing ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return <NotFoundPage />;
  }
  if (detail.isError) {
    return (
      <Shell title={title}>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell title={title}>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  const back = (
    <BackLink to={planPath(planId)} label={t('learningPlans.backToPlan')} />
  );
  if (!detail.data.can_edit) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {back}
        <Shell title={title}>
          <p role="status" className="text-sm">
            {t('learningPlans.editor.cannotEdit')}
          </p>
        </Shell>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {back}
      <LearningPlanEditor
        key={generation}
        plan={detail.data}
        today={todayInZone(timeZone)}
        studentName={student.data ? fullName(student.data) : null}
        focusHeadingOnMount={generation > 0}
        onReload={() => {
          void detail.refetch().then(() => setGeneration((n) => n + 1));
        }}
        onSave={async (values, version) => {
          try {
            const saved = await replacePlan(
              planId,
              replaceBody(values, version ?? detail.data.version),
            );
            client.setQueryData(key, saved);
            void invalidatePlanLists(client);
            void navigate(planPath(planId), {
              replace: true,
              state: { notice: 'planSaved' },
            });
          } catch (caught) {
            if (caught instanceof ApiError && caught.status === 404) {
              setMissing(true);
            }
            throw caught;
          }
        }}
      />
    </div>
  );
}
