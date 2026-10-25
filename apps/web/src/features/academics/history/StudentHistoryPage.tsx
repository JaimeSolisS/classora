import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan, useSession } from '@/features/auth/useSession';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { LINK_BUTTON_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import { cn } from '@/lib/utils';
import { newEvaluationPath } from '../evaluations/api';
import { AcademicHistory } from './AcademicHistory';
import { fetchWorkingList, workingKey, type WorkingStatus } from './api';
import { parseTab, type HistoryTab } from './urlState';
import { WorkingList, type WorkingState } from './WorkingList';

// /students/:studentId/history (#308 E8, D2; E1, E2 (a)), for staff and
// tutors (every role holds student.view; the API decides): the h1
// "Academic history" with the student's name once GET /students/{id}
// answers (without it otherwise: a former author still reads their own
// entries), "Back to student", "New evaluation" for tutors, and two tabs
// (?tab=, the tablist pattern with roving focus): History (the shared
// AcademicHistory) and Evaluations (the working list). Code-split by the
// router.

const TABS: readonly HistoryTab[] = ['history', 'evaluations'];

function useWorkingList(
  studentId: string,
  status: WorkingStatus | null,
  enabled: boolean,
): WorkingState {
  const list = useInfiniteQuery({
    queryKey: workingKey(studentId, status),
    queryFn: ({ pageParam, signal }) =>
      fetchWorkingList(studentId, status, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
    retry: false,
  });
  const missing = list.error instanceof ApiError && list.error.status === 404;
  if (missing) {
    return {
      status: 'ready',
      items: [],
      hasMore: false,
      loadingMore: false,
      onLoadMore: () => undefined,
    };
  }
  if (list.isError) {
    return {
      status: 'error',
      error: list.error,
      onRetry: () => void list.refetch(),
    };
  }
  if (!list.data) {
    return { status: 'loading' };
  }
  return {
    status: 'ready',
    items: list.data.pages.flatMap((page) => page.items),
    hasMore: list.hasNextPage,
    loadingMore: list.isFetchingNextPage,
    onLoadMore: () => void list.fetchNextPage(),
  };
}

export function StudentHistoryPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const location = useLocation();
  const session = useSession();
  const canReview = useCan('evaluation.manage');
  const isTutor =
    session.status === 'signedIn' &&
    (session.user.roles as readonly string[]).includes('tutor');
  const id = useId();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseTab(searchParams);
  const [status, setStatus] = useState<WorkingStatus | null>(null);
  const tabRefs = useRef<Partial<Record<HistoryTab, HTMLButtonElement | null>>>(
    {},
  );
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const arrived = (location.state as { notice?: string } | null)?.notice;
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
  });
  const name = student.data ? fullName(student.data) : null;
  const title = name
    ? t('academics.history.titleFor', { name })
    : t('academics.history.title');
  useDocumentTitle(title);
  const working = useWorkingList(studentId, status, tab === 'evaluations');
  const statuses: WorkingStatus[] = [
    'final',
    ...(isTutor ? (['draft'] as const) : []),
    ...(canReview ? (['voided'] as const) : []),
  ];

  useEffect(() => {
    if (arrived === 'evaluationDeleted') {
      noticeRef.current?.focus();
    }
  }, [arrived]);

  function selectTab(next: HistoryTab) {
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      if (next === 'history') {
        params.delete('tab');
      } else {
        params.set('tab', next);
      }
      return params;
    });
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    const index = TABS.indexOf(tab);
    let next: HistoryTab | undefined;
    if (event.key === 'ArrowRight') {
      next = TABS[(index + 1) % TABS.length];
    } else if (event.key === 'ArrowLeft') {
      next = TABS[(index - 1 + TABS.length) % TABS.length];
    } else if (event.key === 'Home') {
      next = TABS[0];
    } else if (event.key === 'End') {
      next = TABS[TABS.length - 1];
    }
    if (next) {
      event.preventDefault();
      selectTab(next);
      tabRefs.current[next]?.focus();
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-3">
        {student.data ? (
          <Link
            to={`/students/${studentId}`}
            className={`${TEXT_LINK_CLASS} self-start text-sm`}
          >
            {t('academics.history.backToStudent')}
          </Link>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold break-words">{title}</h1>
          {isTutor && student.data ? (
            <Link
              to={newEvaluationPath(studentId)}
              className={LINK_BUTTON_CLASS}
            >
              {t('academics.history.newEvaluation')}
            </Link>
          ) : null}
        </div>
        {arrived === 'evaluationDeleted' ? (
          <p
            ref={noticeRef}
            role="status"
            tabIndex={-1}
            className="text-sm font-medium outline-none"
          >
            {t('academics.evaluations.notices.deleted')}
          </p>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-4">
        <div
          role="tablist"
          aria-label={t('academics.history.tabs.label')}
          className="flex flex-wrap gap-1 border-b border-border"
        >
          {TABS.map((entry) => (
            <button
              key={entry}
              ref={(element) => {
                tabRefs.current[entry] = element;
              }}
              type="button"
              role="tab"
              id={`${id}-tab-${entry}`}
              aria-selected={tab === entry}
              aria-controls={`${id}-panel`}
              tabIndex={tab === entry ? 0 : -1}
              onClick={() => selectTab(entry)}
              onKeyDown={onTabKey}
              className={cn(
                'inline-flex min-h-9 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring',
                tab === entry
                  ? 'border-foreground text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              {t(`academics.history.tabs.${entry}`)}
            </button>
          ))}
        </div>
        <div
          role="tabpanel"
          id={`${id}-panel`}
          aria-labelledby={`${id}-tab-${tab}`}
          className="min-w-0"
        >
          {tab === 'history' ? (
            <AcademicHistory
              studentId={studentId}
              audience="staff"
              studentName={name ?? ''}
            />
          ) : (
            <WorkingList
              state={working}
              status={status}
              onStatus={setStatus}
              statuses={statuses}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default StudentHistoryPage;
