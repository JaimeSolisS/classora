import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { useCan, useSession } from '@/features/auth/useSession';
import {
  fetchSubjectOptions,
  SCHEDULE_OPTIONS_KEY,
} from '@/features/scheduling/create/api';
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import { fetchGroups, groupsListKey, type GroupListFilters } from './api';
import { GroupsTable, type GroupsTableState } from './GroupsTable';
import {
  filterParams,
  groupView,
  hasFilters,
  listFilters,
  SEARCH_MAX_LENGTH,
  STATUS_FILTERS,
} from './rules';

// /groups (#234 B): the staff list (schedule.view) or the student's own
// groups ("My groups", only the search). The filters live in the URL (q,
// subject, status; Active is the default and left out), so a reload and
// Back keep them; the search waits 300 ms. A filter change starts the list
// again from its first page (the cursor is part of the query, not the
// URL). "New group" for schedule.manage holders.

/** How long typing must pause before the search runs. */
export const SEARCH_DEBOUNCE_MS = 300;

export function GroupsPage() {
  const { t } = useTranslation();
  const session = useSession();
  const view = groupView(session.status === 'signedIn' ? session.user : null);
  const staff = view === 'staff';
  const title = staff ? t('groups.title') : t('groups.myTitle');
  useDocumentTitle(title);
  const canManage = useCan('schedule.manage');
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    subject: `${id}-subject`,
    status: `${id}-status`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const parsed = listFilters(searchParams);
  // A student filters by name only (B5).
  const filters: GroupListFilters = staff
    ? parsed
    : { q: parsed.q, subject: '', status: 'active' };
  const [text, setText] = useState(filters.q);
  const [shownQ, setShownQ] = useState(filters.q);
  if (filters.q !== shownQ) {
    // Back/forward or a link changed q: show it in the field.
    setShownQ(filters.q);
    if (text.trim() !== filters.q) {
      setText(filters.q);
    }
  }

  function apply(next: GroupListFilters, options?: { replace?: boolean }) {
    setSearchParams(filterParams(next), options);
  }

  useEffect(() => {
    const q = text.trim().slice(0, SEARCH_MAX_LENGTH);
    if (q === filters.q) {
      return;
    }
    const timer = window.setTimeout(() => {
      apply({ ...filters, q }, { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // apply only uses the stable setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, filters.q]);

  const subjects = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: staff,
  });
  const list = useInfiniteQuery({
    queryKey: groupsListKey(filters),
    queryFn: ({ pageParam, signal }) => fetchGroups(filters, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  });

  const tableState: GroupsTableState = list.data
    ? {
        status: 'ready',
        items: list.data.pages.flatMap((page) => page.items),
        hasMore: list.hasNextPage,
        loadingMore: list.isFetchingNextPage,
        onMore: () => void list.fetchNextPage(),
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  function clearFilters() {
    setText('');
    apply({ q: '', subject: '', status: 'active' });
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {title}
        </h1>
        {staff && canManage ? (
          <Link to="/groups/new" className={LINK_BUTTON_CLASS}>
            {t('groups.new')}
          </Link>
        ) : null}
      </div>
      <div
        role="search"
        className={
          staff
            ? 'grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]'
            : 'grid grid-cols-1 gap-4 md:max-w-md'
        }
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('groups.list.search')}
          </label>
          <Input
            id={ids.search}
            type="search"
            autoComplete="off"
            spellCheck={false}
            maxLength={SEARCH_MAX_LENGTH}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        {staff ? (
          <>
            <div className="flex flex-col gap-2">
              <label htmlFor={ids.subject} className="text-sm font-medium">
                {t('groups.list.subject')}
              </label>
              <select
                id={ids.subject}
                className={SELECT_CLASS}
                value={filters.subject}
                onChange={(event) =>
                  apply({ ...filters, subject: event.target.value })
                }
              >
                <option value="">{t('groups.list.allSubjects')}</option>
                {subjectsByLevel(subjects.data ?? []).map(([level, items]) => (
                  <optgroup key={level} label={level}>
                    {items.map((subject) => (
                      <option key={subject.id} value={subject.id}>
                        {subject.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor={ids.status} className="text-sm font-medium">
                {t('groups.list.status')}
              </label>
              <select
                id={ids.status}
                className={SELECT_CLASS}
                value={filters.status}
                onChange={(event) =>
                  apply({
                    ...filters,
                    status: event.target.value as GroupListFilters['status'],
                  })
                }
              >
                {STATUS_FILTERS.map((value) => (
                  <option key={value} value={value}>
                    {t(`groups.list.statusOptions.${value}`)}
                  </option>
                ))}
              </select>
            </div>
          </>
        ) : null}
      </div>
      <GroupsTable
        state={tableState}
        view={view}
        statusFilter={filters.status}
        labelledBy={ids.title}
        filtered={hasFilters(filters)}
        onClearFilters={clearFilters}
      />
    </div>
  );
}
