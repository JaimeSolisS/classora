import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly } from './access';
import {
  assignmentKey,
  assignmentsPath,
  deleteAssignment,
  fetchAssignment,
  invalidateAssignmentLists,
  myHomeworkItemPath,
  putGrade,
  putSubmission,
  type AssignmentView,
} from './api';
import { RosterView } from './RosterView';

// /assignments/:assignmentId (#313 C2-C5), for the creator and
// assignment.review holders (an unknown, deleted or hidden assignment
// shows the not-found page). A grade replaces its row in the cache;
// recorded work reads the roster again; a delete goes (replace) to
// /assignments with "Homework deleted.". Arriving from the form ("Homework
// set. …" / "Homework saved.") focuses that notice. Reload refetches, then
// remounts the view, which focuses its heading. A student or guardian goes
// to their own page of the same homework. Code-split.

interface ArrivalState {
  notice?: 'created' | 'saved';
  count?: number;
}

function Shell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t('assignments.rosterTitle')}</h1>
      {children}
    </div>
  );
}

export function RosterPage() {
  const { t } = useTranslation();
  const { assignmentId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const familyOnly = useFamilyOnly();
  const [generation, setGeneration] = useState(0);
  const [missing, setMissing] = useState(false);
  const key = assignmentKey(assignmentId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchAssignment(assignmentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  useDocumentTitle(detail.data?.title ?? t('assignments.rosterTitle'));
  const arrived = location.state as ArrivalState | null;

  if (familyOnly) {
    return <Navigate replace to={myHomeworkItemPath(assignmentId)} />;
  }
  if (
    missing ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return <NotFoundPage />;
  }
  if (detail.isError) {
    return (
      <Shell>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell>
        <LoadingState rows={6} />
      </Shell>
    );
  }

  const notFoundOn404 = (caught: unknown) => {
    if (caught instanceof ApiError && caught.status === 404) {
      setMissing(true);
    }
  };
  const notice =
    generation > 0
      ? null
      : arrived?.notice === 'created'
        ? t('assignments.notices.created', { count: arrived.count ?? 0 })
        : arrived?.notice === 'saved'
          ? t('assignments.notices.saved')
          : null;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link to={assignmentsPath()} className={`${SMALL_LINK_CLASS} self-start`}>
        {t('assignments.backToList')}
      </Link>
      <RosterView
        key={`${assignmentId}-${generation}`}
        assignment={detail.data}
        initialNotice={notice}
        focusHeadingOnMount={generation > 0}
        actions={{
          onGrade: async (row, body) => {
            try {
              const stored = await putGrade(assignmentId, row.student.id, body);
              client.setQueryData<AssignmentView>(key, (current) =>
                current
                  ? {
                      ...current,
                      roster: current.roster.map((item) =>
                        item.student.id === stored.student.id ? stored : item,
                      ),
                    }
                  : current,
              );
              // The counts follow from the server.
              void client.invalidateQueries({ queryKey: key });
              void invalidateAssignmentLists(client);
            } catch (caught) {
              notFoundOn404(caught);
              throw caught;
            }
          },
          onRecord: async (row, body) => {
            try {
              await putSubmission(assignmentId, row.student.id, body);
            } catch (caught) {
              notFoundOn404(caught);
              throw caught;
            }
            await detail.refetch();
            void invalidateAssignmentLists(client);
          },
          onDelete: async () => {
            const current =
              client.getQueryData<AssignmentView>(key) ?? detail.data;
            try {
              await deleteAssignment(current);
            } catch (caught) {
              notFoundOn404(caught);
              throw caught;
            }
            client.removeQueries({ queryKey: key });
            void invalidateAssignmentLists(client);
            void navigate(assignmentsPath(), {
              replace: true,
              state: { notice: 'deleted' },
            });
          },
          onReload: () => {
            // Remount once the fresh roster is in the cache: the view then
            // focuses its heading on mount (deterministic, #308 QA D4).
            void detail.refetch().then(() => setGeneration((n) => n + 1));
          },
        }}
      />
    </div>
  );
}

export default RosterPage;
