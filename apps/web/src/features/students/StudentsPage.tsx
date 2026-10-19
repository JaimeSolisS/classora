import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { useCan } from '@/features/auth/useSession';
import {
  ACTIVE_LEVELS_QUERY_KEY,
  fetchActiveLevels,
  fetchStudents,
  studentsListKey,
} from './api';
import { listParams, SEARCH_MAX_LENGTH } from './rules';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from './styles';
import { StudentsTable, type StudentsTableState } from './StudentsTable';

// /students (#33 I): name search, education level and status filters, and
// the paginated list. The filters live in the URL (q, level, status, page),
// so a reload and Back keep them. Typing in the search waits 300 ms before it
// changes the URL and goes back to page 1. Every student.view holder sees
// the page; the API decides which students they see (tutors, guardians and
// students get an empty list until M4, #34 and #35).

/** How long typing must pause before the search runs. */
export const SEARCH_DEBOUNCE_MS = 300;

export function StudentsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('students.title'));
  const canManage = useCan('student.manage');
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    level: `${id}-level`,
    status: `${id}-status`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const params = listParams(searchParams);
  const [text, setText] = useState(params.q);
  const [shownQ, setShownQ] = useState(params.q);
  if (params.q !== shownQ) {
    // Back/forward or a link changed q: show it in the field (adjusting
    // state while rendering, not in an effect).
    setShownQ(params.q);
    if (text.trim() !== params.q) {
      setText(params.q);
    }
  }

  /** Sets one filter (empty removes it) and goes back to page 1. */
  function setFilter(
    name: 'q' | 'level' | 'status',
    value: string,
    options?: { replace?: boolean },
  ) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) {
        next.set(name, value);
      } else {
        next.delete(name);
      }
      next.delete('page');
      return next;
    }, options);
  }

  useEffect(() => {
    const q = text.trim();
    if (q === params.q) {
      return;
    }
    const timer = window.setTimeout(() => {
      setFilter('q', q, { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // setFilter only uses the stable setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, params.q]);

  function goToPage(page: number) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (page > 1) {
        next.set('page', String(page));
      } else {
        next.delete('page');
      }
      return next;
    });
  }

  const levels = useQuery({
    queryKey: ACTIVE_LEVELS_QUERY_KEY,
    queryFn: ({ signal }) => fetchActiveLevels(signal),
  });
  const list = useQuery({
    queryKey: studentsListKey(params),
    queryFn: ({ signal }) => fetchStudents(params, signal),
    placeholderData: keepPreviousData,
  });

  const tableState: StudentsTableState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  const levelOptions = levels.data?.items ?? [];

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('students.title')}
        </h1>
        {canManage ? (
          <Link to="/students/new" className={LINK_BUTTON_CLASS}>
            {t('students.add')}
          </Link>
        ) : null}
      </div>
      <div
        role="search"
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('students.list.search')}
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
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.level} className="text-sm font-medium">
            {t('students.list.level')}
          </label>
          <select
            id={ids.level}
            className={SELECT_CLASS}
            value={params.level}
            onChange={(event) => setFilter('level', event.target.value)}
          >
            <option value="">{t('students.list.allLevels')}</option>
            {levelOptions.map((level) => (
              <option key={level.id} value={level.id}>
                {level.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.status} className="text-sm font-medium">
            {t('students.list.status')}
          </label>
          <select
            id={ids.status}
            className={SELECT_CLASS}
            value={params.status}
            onChange={(event) =>
              setFilter(
                'status',
                event.target.value === 'active' ? '' : event.target.value,
              )
            }
          >
            <option value="active">{t('students.status.active')}</option>
            <option value="inactive">{t('students.status.inactive')}</option>
            <option value="all">{t('students.list.statusAll')}</option>
          </select>
        </div>
      </div>
      <StudentsTable
        state={tableState}
        labelledBy={ids.title}
        searching={params.q !== ''}
        onPage={goToPage}
      />
    </div>
  );
}
