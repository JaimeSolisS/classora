import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import {
  fetchStudentAssignments,
  studentListKey,
  SUBMISSION_STATES,
  type StateSummary,
  type StudentAssignment,
  type SubmissionState,
} from './api';
import { useDeadlineText } from './DeadlineText';
import { parseState } from './rules';
import { LateMarker, StateBadge } from './StateBadge';

// One student's homework (#313 D1, E2, E3, E8), shared by the staff and
// tutor page and the family page: the API's summary as four filter buttons
// ("Pending {n}", "Missing {n}", "Handed in {n}", "Graded {n}", aria-
// pressed, kept as ?state=) plus "All", then the rows in the API's order
// (the title linking to the detail, the subject, the deadline text, the
// state badge, "Late", "{score} of {max}" once graded) and "Load more" (20
// per page). States: loading, error with "Try again", consent required
// (403 student_consent_required), "This homework list isn't available."
// (404, inline), empty, and empty with a filter ("Show all", which keeps
// focus on the "All" button).

export interface StudentAssignmentsListProps {
  studentId: string;
  /** The detail of an item. */
  linkTo: (assignmentId: string) => string;
  /** The student's name for the consent message. */
  studentName?: string;
  /** "No homework yet." for this audience. */
  emptyText: string;
  /** Read `now` for the deadline text (tests). */
  now?: () => number;
}

export function StateFilter({
  summary,
  state,
  onState,
}: {
  summary: StateSummary;
  state: SubmissionState | null;
  onState: (state: SubmissionState | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="group"
      aria-label={t('assignments.summary.label')}
      className="flex flex-wrap gap-2"
    >
      {SUBMISSION_STATES.map((value) => (
        <Button
          key={value}
          type="button"
          size="sm"
          variant={state === value ? 'default' : 'outline'}
          aria-pressed={state === value}
          onClick={() => onState(value)}
        >
          {t(`assignments.summary.${value}`, { n: summary[value] })}
        </Button>
      ))}
      <Button
        type="button"
        size="sm"
        data-all=""
        variant={state === null ? 'default' : 'outline'}
        aria-pressed={state === null}
        onClick={() => onState(null)}
      >
        {t('assignments.summary.all')}
      </Button>
    </div>
  );
}

export function StudentAssignmentRow({
  item,
  to,
  now,
}: {
  item: StudentAssignment;
  to: string;
  now?: () => number;
}) {
  const { t } = useTranslation();
  const deadlineText = useDeadlineText(now);
  const { submission } = item;
  return (
    <li className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border p-4 text-sm">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <h2 className="min-w-0 text-base font-semibold break-words">
          <Link
            to={to}
            className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {item.title}
          </Link>
        </h2>
        <span className="flex flex-wrap gap-1">
          <StateBadge state={submission.state} />
          <LateMarker late={submission.is_late} />
        </span>
      </div>
      <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1">
        <span className="break-words">{item.subject.name}</span>
        <span>
          {deadlineText({
            ...item,
            state: submission.state,
            is_late: submission.is_late,
          })}
        </span>
      </p>
      {submission.state === 'graded' &&
      submission.score !== null &&
      item.max_score !== null ? (
        <p className="font-medium">
          {t('assignments.score', {
            score: submission.score,
            max: item.max_score,
          })}
        </p>
      ) : null}
    </li>
  );
}

export function StudentAssignmentsList({
  studentId,
  linkTo,
  studentName = '',
  emptyText,
  now,
}: StudentAssignmentsListProps) {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const state = parseState(searchParams.get('state'));
  const rootRef = useRef<HTMLDivElement>(null);
  const [focusAll, setFocusAll] = useState(0);
  const list = useInfiniteQuery({
    queryKey: studentListKey(studentId, state),
    queryFn: ({ pageParam, signal }) =>
      fetchStudentAssignments(studentId, state, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    // The buttons stay while another state loads (focus stays on them).
    placeholderData: keepPreviousData,
    retry: false,
  });

  useEffect(() => {
    if (focusAll === 0) {
      return;
    }
    const timer = window.setTimeout(
      () => rootRef.current?.querySelector<HTMLElement>('[data-all]')?.focus(),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [focusAll]);

  const onState = (next: SubmissionState | null) => {
    const params = new URLSearchParams(searchParams);
    if (next) {
      params.set('state', next);
    } else {
      params.delete('state');
    }
    setSearchParams(params, { replace: true });
  };

  if (
    list.error instanceof ApiError &&
    list.error.code === 'student_consent_required'
  ) {
    return (
      <ConsentRequired
        name={studentName}
        title={t('academics.history.consentTitle')}
      />
    );
  }
  if (list.error instanceof ApiError && list.error.status === 404) {
    return (
      <p role="status" className="text-sm">
        {t('assignments.student.listMissing')}
      </p>
    );
  }
  if (list.isError) {
    return (
      <ErrorAlert error={list.error} onRetry={() => void list.refetch()} />
    );
  }
  if (!list.data) {
    return <LoadingState rows={4} />;
  }
  const summary = list.data.pages[0]!.summary;
  const items = list.data.pages.flatMap((page) => page.items);
  return (
    <div ref={rootRef} className="flex min-w-0 flex-col gap-4">
      <StateFilter summary={summary} state={state} onState={onState} />
      {items.length === 0 ? (
        <div className="flex flex-col items-start gap-2">
          <p role="status" className="text-sm">
            {state ? t('assignments.student.emptyFiltered') : emptyText}
          </p>
          {state ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                onState(null);
                setFocusAll((n) => n + 1);
              }}
            >
              {t('assignments.student.showAll')}
            </Button>
          ) : null}
        </div>
      ) : (
        <ul className="flex min-w-0 flex-col gap-3">
          {items.map((item) => (
            <StudentAssignmentRow
              key={item.id}
              item={item}
              to={linkTo(item.id)}
              now={now}
            />
          ))}
        </ul>
      )}
      {list.hasNextPage ? (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={list.isFetchingNextPage}
            onClick={() => void list.fetchNextPage()}
          >
            {list.isFetchingNextPage ? (
              <Spinner label={t('assignments.loadingMore')} showLabel />
            ) : (
              t('assignments.loadMore')
            )}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
