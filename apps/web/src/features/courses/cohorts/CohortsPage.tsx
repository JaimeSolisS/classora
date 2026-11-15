import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from '@tanstack/react-query';
import { useEffect, useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { useCan } from '@/features/auth/useSession';
import {
  fetchSubjectOptions,
  SCHEDULE_OPTIONS_KEY,
} from '@/features/scheduling/create/api';
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import { CoursesTabs } from '../CoursesTabs';
import {
  cohortsListKey,
  fetchCohorts,
  fetchTemplateOptions,
  templateOptionsKey,
  type CohortListFilters,
} from './api';
import { CohortsTable, type CohortsTableState } from './CohortsTable';
import {
  DEFAULT_FILTERS,
  filterParams,
  hasFilters,
  listFilters,
  SEARCH_MAX_LENGTH,
  STATUS_FILTERS,
} from './rules';

// /courses/cohorts (#243 B), for course.view holders. The filters live in
// the URL (q, template, subject, status; "Upcoming and in progress" is the
// default and left out), so a reload and Back keep them; the search waits
// 300 ms. A filter change starts the list again from its first page (the
// cursor is part of the query, not the URL). The template filter lists
// every template, inactive ones marked. "New cohort" for schedule.manage.
//
// "My courses" (#246 B2): a tutor, student or guardian without course.view
// gets the cohorts they teach or take (the API decides), read-only, with the
// name and status filters only (the template and subject options need
// course.view and catalog access).

/** How long typing must pause before the search runs. */
export const SEARCH_DEBOUNCE_MS = 300;

export function CohortsPage() {
  const { t } = useTranslation();
  const staff = useCan('course.view');
  const title = staff
    ? t('courses.cohorts.title')
    : t('courses.cohorts.myTitle');
  useDocumentTitle(title);
  const canManage = useCan('schedule.manage');
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    template: `${id}-template`,
    subject: `${id}-subject`,
    status: `${id}-status`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const parsed = listFilters(searchParams);
  const filters: CohortListFilters = staff
    ? parsed
    : { ...parsed, template: '', subject: '' };
  const [text, setText] = useState(filters.q);
  const [shownQ, setShownQ] = useState(filters.q);
  if (filters.q !== shownQ) {
    // Back/forward or a link changed q: show it in the field.
    setShownQ(filters.q);
    if (text.trim() !== filters.q) {
      setText(filters.q);
    }
  }

  function apply(next: CohortListFilters, options?: { replace?: boolean }) {
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

  const templates = useQuery({
    queryKey: templateOptionsKey('all'),
    queryFn: ({ signal }) => fetchTemplateOptions('all', signal),
    enabled: staff,
  });
  const subjects = useQuery({
    queryKey: [...SCHEDULE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: staff,
  });
  const list = useInfiniteQuery({
    queryKey: cohortsListKey(filters),
    queryFn: ({ pageParam, signal }) =>
      fetchCohorts(filters, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  });

  const tableState: CohortsTableState = list.data
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
    apply(DEFAULT_FILTERS);
  }

  function select(
    name: 'template' | 'subject' | 'status',
    label: string,
    value: string,
    onChange: (value: string) => void,
    options: ReactNode,
  ) {
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={ids[name]} className="text-sm font-medium">
          {label}
        </label>
        <select
          id={ids[name]}
          className={SELECT_CLASS}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {options}
        </select>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <CoursesTabs />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {title}
        </h1>
        {canManage ? (
          <Link to="/courses/cohorts/new" className={LINK_BUTTON_CLASS}>
            {t('courses.cohorts.new')}
          </Link>
        ) : null}
      </div>
      <div
        role="search"
        className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('courses.cohorts.list.search')}
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
        {staff
          ? select(
              'template',
              t('courses.cohorts.list.template'),
              filters.template,
              (template) => apply({ ...filters, template }),
              <>
                <option value="">
                  {t('courses.cohorts.list.allTemplates')}
                </option>
                {(templates.data ?? []).map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.is_active
                      ? template.name
                      : t('courses.cohorts.list.inactiveTemplate', {
                          name: template.name,
                        })}
                  </option>
                ))}
              </>,
            )
          : null}
        {staff
          ? select(
              'subject',
              t('courses.cohorts.list.subject'),
              filters.subject,
              (subject) => apply({ ...filters, subject }),
              <>
                <option value="">
                  {t('courses.cohorts.list.allSubjects')}
                </option>
                {subjectsByLevel(subjects.data ?? []).map(([level, items]) => (
                  <optgroup key={level} label={level}>
                    {items.map((subject) => (
                      <option key={subject.id} value={subject.id}>
                        {subject.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </>,
            )
          : null}
        {select(
          'status',
          t('courses.cohorts.list.status'),
          filters.status,
          (status) =>
            apply({
              ...filters,
              status: status as CohortListFilters['status'],
            }),
          STATUS_FILTERS.map((value) => (
            <option key={value} value={value}>
              {t(`courses.cohorts.list.statusOptions.${value}`)}
            </option>
          )),
        )}
      </div>
      <CohortsTable
        state={tableState}
        labelledBy={ids.title}
        filtered={hasFilters(filters)}
        onClearFilters={clearFilters}
      />
    </div>
  );
}
