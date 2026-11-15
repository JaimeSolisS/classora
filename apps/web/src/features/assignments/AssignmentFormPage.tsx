import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { uuidParam } from '@/features/academics/history/urlState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { todayInZone, toLocalTime } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import {
  assignmentKey,
  assignmentsPath,
  createAssignment,
  fetchAssignment,
  fetchSession,
  invalidateAssignmentLists,
  newAssignmentPath,
  patchAssignment,
  rosterPath,
  sessionKey,
} from './api';
import { AssignmentForm, type SessionContext } from './AssignmentForm';
import { createBody, patchBody } from './rules';

// /assignments/new[?sessionId=] (#313 B1, B5) and /assignments/:id/edit
// (#313 B4), for tutors (the route's handle; the API decides). Code-split.
//
// New: with ?sessionId=, the class's attendance read (the current tutor's:
// its state, subject, start and participants) fixes the subject and checks
// the participants; a 404 or a state other than confirmed / completed shows
// "Homework can't be linked to this class." with a link to the form without
// it, and no form. Without a class: the active subjects (GET /subjects).
// After the create the page goes (replace) to the roster, which says
// "Homework set. {n} students were notified." (focused).
//
// Edit: the assignment (404 → not-found page); without `can_edit`, "This
// homework can't be edited." and a link to the roster, no form. A class's
// homework offers its participants (else the search). After the save the
// page goes (replace) to the roster, which says "Homework saved.".
//
// Reload refetches, then remounts the form, which focuses its heading.

const ASSIGNABLE = new Set(['confirmed', 'completed']);

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex w-full max-w-2xl min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold break-words">{title}</h1>
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

function useClock() {
  const zone = useCenterTimeZone();
  const now = new Date();
  return {
    today: todayInZone(zone, now),
    nowTime: toLocalTime(now.toISOString(), zone),
  };
}

export function NewAssignmentPage() {
  const { t } = useTranslation();
  const title = t('assignments.form.newTitle');
  useDocumentTitle(title);
  const [searchParams] = useSearchParams();
  const rawSession = searchParams.get('sessionId');
  const sessionId = uuidParam(rawSession);
  return (
    <NewAssignmentForm
      key={rawSession ?? ''}
      rawSession={rawSession}
      sessionId={sessionId}
      title={title}
    />
  );
}

function NewAssignmentForm({
  rawSession,
  sessionId,
  title,
}: {
  rawSession: string | null;
  sessionId: string | null;
  title: string;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const clock = useClock();
  const [generation, setGeneration] = useState(0);
  const session = useQuery({
    queryKey: sessionKey(sessionId ?? ''),
    queryFn: ({ signal }) => fetchSession(sessionId ?? '', signal),
    enabled: sessionId !== null,
    retry: false,
  });
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: rawSession === null,
  });
  const back = (
    <BackLink to={assignmentsPath()} label={t('assignments.backToList')} />
  );

  const sessionMissing =
    (rawSession !== null && sessionId === null) ||
    (session.error instanceof ApiError && session.error.status === 404) ||
    (session.data !== undefined && !ASSIGNABLE.has(session.data.state));
  if (sessionMissing) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {back}
        <Shell title={title}>
          <p role="status" className="text-sm">
            {t('assignments.form.sessionInvalid')}
          </p>
          <BackLink
            to={newAssignmentPath()}
            label={t('assignments.form.withoutSession')}
          />
        </Shell>
      </div>
    );
  }
  const failed = session.isError
    ? { error: session.error, retry: () => void session.refetch() }
    : subjects.isError
      ? { error: subjects.error, retry: () => void subjects.refetch() }
      : null;
  if (failed) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {back}
        <Shell title={title}>
          <ErrorAlert error={failed.error} onRetry={failed.retry} />
        </Shell>
      </div>
    );
  }
  if (
    (sessionId !== null && !session.data) ||
    (rawSession === null && !subjects.data)
  ) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {back}
        <Shell title={title}>
          <LoadingState rows={6} />
        </Shell>
      </div>
    );
  }
  const context: SessionContext | null = session.data
    ? {
        id: session.data.class_session_id,
        startsAt: session.data.starts_at,
        subject: {
          id: session.data.subject.id,
          name: session.data.subject.name,
          level: session.data.subject.education_level_name,
        },
        participants: session.data.items.map((item) => ({
          id: item.student.id,
          name: item.student.display_name,
        })),
      }
    : null;
  const choices = (subjects.data ?? []).map((subject) => ({
    id: subject.id,
    label: t('academics.subjectOption', {
      subject: subject.name,
      level: subject.levelName,
    }),
  }));

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {back}
      <AssignmentForm
        key={generation}
        assignment={null}
        session={context}
        subjects={choices}
        today={clock.today}
        nowTime={clock.nowTime}
        withoutSessionPath={newAssignmentPath()}
        focusHeadingOnMount={generation > 0}
        onReload={() => {
          const reads: Promise<unknown>[] = [];
          if (sessionId !== null) reads.push(session.refetch());
          if (rawSession === null) reads.push(subjects.refetch());
          void Promise.all(reads).then(() => setGeneration((n) => n + 1));
        }}
        onSave={async (values) => {
          const created = await createAssignment(
            createBody(values, context?.id ?? null),
          );
          client.setQueryData(assignmentKey(created.id), created);
          void invalidateAssignmentLists(client);
          void navigate(rosterPath(created.id), {
            replace: true,
            state: { notice: 'created', count: created.counts.recipients },
          });
        }}
      />
    </div>
  );
}

export function EditAssignmentPage() {
  const { t } = useTranslation();
  const { assignmentId = '' } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const clock = useClock();
  const [generation, setGeneration] = useState(0);
  const [missing, setMissing] = useState(false);
  const key = assignmentKey(assignmentId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchAssignment(assignmentId, signal),
    retry: false,
  });
  const linkedSession = detail.data?.class_session?.id ?? null;
  const session = useQuery({
    queryKey: sessionKey(linkedSession ?? ''),
    queryFn: ({ signal }) => fetchSession(linkedSession ?? '', signal),
    enabled: linkedSession !== null && detail.data?.can_edit === true,
    retry: false,
  });
  const title = t('assignments.form.editTitle');
  useDocumentTitle(title);

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
  if (
    !detail.data ||
    (linkedSession !== null && detail.data.can_edit && session.isPending)
  ) {
    return (
      <Shell title={title}>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  const back = (
    <BackLink
      to={rosterPath(assignmentId)}
      label={t('assignments.backToRoster')}
    />
  );
  if (!detail.data.can_edit) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {back}
        <Shell title={title}>
          <p role="status" className="text-sm">
            {t('assignments.form.cannotEdit')}
          </p>
        </Shell>
      </div>
    );
  }
  // A class's homework: its participants, when the class still reads.
  const participants = session.data
    ? session.data.items.map((item) => ({
        id: item.student.id,
        name: item.student.display_name,
      }))
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {back}
      <AssignmentForm
        key={generation}
        assignment={detail.data}
        participants={participants}
        today={clock.today}
        nowTime={clock.nowTime}
        withoutSessionPath={newAssignmentPath()}
        focusHeadingOnMount={generation > 0}
        onReload={() => {
          void detail.refetch().then(() => setGeneration((n) => n + 1));
        }}
        onSave={async (values, original, version) => {
          try {
            const saved = await patchAssignment(
              assignmentId,
              patchBody(values, original!, version ?? detail.data.version),
            );
            client.setQueryData(key, saved);
            void invalidateAssignmentLists(client);
            void navigate(rosterPath(assignmentId), {
              replace: true,
              state: { notice: 'saved' },
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
