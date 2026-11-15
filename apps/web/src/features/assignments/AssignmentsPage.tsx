import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { uuidParam } from '@/features/academics/history/urlState';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import { useFamilyOnly, useRoles } from './access';
import {
  fetchWorkingList,
  myHomeworkPath,
  newAssignmentPath,
  rosterPath,
  workingListKey,
  type AssignmentSummary,
  type DueFilter,
} from './api';
import { useCountsText } from './Counts';
import { useDueText } from './DeadlineText';

// /assignments (#313 C1), for tutors (the homework they set) and
// assignment.review holders (every one; the API decides): the h1
// "Assigned homework", "Set homework" for tutor-role users, the filters in
// the URL (?due= upcoming by default, past or all; ?subject=), the rows as
// cards (title linking to the roster, subject, deadline, the creator for
// someone else's homework, the counts as text) and "Load more" (20 per
// page). Empty: "No homework yet." (+ "Set homework") or, with filters,
// "No homework matches these filters." + "Clear filters" (focus then goes
// to the heading). After a delete, "Homework deleted." takes focus. A
// student or guardian goes to /my-homework. Code-split.

const DUES: readonly DueFilter[] = ['upcoming', 'past', 'all'];

function parseDue(value: string | null): DueFilter {
  return DUES.find((due) => due === value) ?? 'upcoming';
}

function Row({ item }: { item: AssignmentSummary }) {
  const { t } = useTranslation();
  const dueText = useDueText();
  const countsText = useCountsText();
  return (
    <li className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border p-4 text-sm">
      <h2 className="min-w-0 text-base font-semibold break-words">
        <Link
          to={rosterPath(item.id)}
          className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {item.title}
        </Link>
      </h2>
      <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1">
        <span className="break-words">{item.subject.name}</span>
        <span>{dueText(item)}</span>
      </p>
      {!item.is_own ? (
        <p className="break-words text-muted-foreground">
          {t('assignments.setBy', { name: item.creator.display_name })}
        </p>
      ) : null}
      <p className="break-words">{countsText(item.counts)}</p>
    </li>
  );
}

export function AssignmentsPage() {
  const { t } = useTranslation();
  const title = t('assignments.listTitle');
  useDocumentTitle(title);
  const id = useId();
  const location = useLocation();
  const familyOnly = useFamilyOnly();
  const tutor = useRoles().includes('tutor');
  const [searchParams, setSearchParams] = useSearchParams();
  const due = parseDue(searchParams.get('due'));
  const subject = uuidParam(searchParams.get('subject'));
  const filtered = due !== 'upcoming' || subject !== null;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const [focus, setFocus] = useState<{
    to: 'notice' | 'heading';
    n: number;
  } | null>(() =>
    (location.state as { notice?: string } | null)?.notice === 'deleted'
      ? { to: 'notice', n: 0 }
      : null,
  );
  const deleted =
    (location.state as { notice?: string } | null)?.notice === 'deleted';
  const list = useInfiniteQuery({
    queryKey: workingListKey(due, subject),
    queryFn: ({ pageParam, signal }) =>
      fetchWorkingList(due, subject, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: !familyOnly,
    retry: false,
  });
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: !familyOnly,
  });

  useEffect(() => {
    if (!focus) {
      return;
    }
    const timer = window.setTimeout(() => {
      (focus.to === 'notice' ? noticeRef.current : headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focus]);

  if (familyOnly) {
    return <Navigate replace to={myHomeworkPath()} />;
  }

  const change = (next: { due?: DueFilter; subject?: string | null }) => {
    const params = new URLSearchParams();
    const nextDue = next.due ?? due;
    const nextSubject = next.subject === undefined ? subject : next.subject;
    if (nextDue !== 'upcoming') params.set('due', nextDue);
    if (nextSubject) params.set('subject', nextSubject);
    setSearchParams(params, { replace: true });
  };
  const setLink = tutor ? (
    <Link
      to={newAssignmentPath()}
      className={`${LINK_BUTTON_CLASS} self-start`}
    >
      {t('assignments.setHomework')}
    </Link>
  ) : null;
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];

  let content;
  if (list.isError) {
    content = (
      <ErrorAlert error={list.error} onRetry={() => void list.refetch()} />
    );
  } else if (!list.data) {
    content = <LoadingState rows={4} />;
  } else if (items.length === 0) {
    content = (
      <div className="flex flex-col items-start gap-3">
        <p role="status" className="text-sm">
          {filtered ? t('assignments.emptyFiltered') : t('assignments.empty')}
        </p>
        {filtered ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setSearchParams(new URLSearchParams(), { replace: true });
              setFocus((current) => ({
                to: 'heading',
                n: (current?.n ?? 0) + 1,
              }));
            }}
          >
            {t('assignments.clearFilters')}
          </Button>
        ) : (
          setLink
        )}
      </div>
    );
  } else {
    content = (
      <>
        <ul className="flex min-w-0 flex-col gap-3">
          {items.map((item) => (
            <Row key={item.id} item={item} />
          ))}
        </ul>
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
      </>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1
        ref={headingRef}
        tabIndex={-1}
        className="text-2xl font-semibold outline-none"
      >
        {title}
      </h1>
      {deleted ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {t('assignments.notices.deleted')}
        </p>
      ) : null}
      {list.data && items.length === 0 && !filtered ? null : setLink}
      <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={`${id}-due`} className="text-sm font-medium">
            {t('assignments.filters.due')}
          </label>
          <select
            id={`${id}-due`}
            className={SELECT_CLASS}
            value={due}
            onChange={(event) => change({ due: parseDue(event.target.value) })}
          >
            {DUES.map((value) => (
              <option key={value} value={value}>
                {t(`assignments.filters.dues.${value}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={`${id}-subject`} className="text-sm font-medium">
            {t('assignments.filters.subject')}
          </label>
          <select
            id={`${id}-subject`}
            className={SELECT_CLASS}
            value={subject ?? ''}
            onChange={(event) =>
              change({ subject: uuidParam(event.target.value) })
            }
          >
            <option value="">{t('assignments.filters.allSubjects')}</option>
            {(subjects.data ?? []).map((option) => (
              <option key={option.id} value={option.id}>
                {t('academics.subjectOption', {
                  subject: option.name,
                  level: option.levelName,
                })}
              </option>
            ))}
            {subject && !(subjects.data ?? []).some((s) => s.id === subject) ? (
              <option value={subject}>
                {t('assignments.filters.selectedSubject')}
              </option>
            ) : null}
          </select>
        </div>
      </div>
      {content}
    </div>
  );
}

export default AssignmentsPage;
