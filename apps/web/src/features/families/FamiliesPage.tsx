import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Input } from '@/components/ui/input';
import { useCan } from '@/features/auth/useSession';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import { familiesListKey, fetchFamilies } from './api';
import { FamiliesTable, type FamiliesTableState } from './FamiliesTable';
import { listParams, SEARCH_MAX_LENGTH } from './rules';

// /families (#172 H1): name search, status filter and the paginated list.
// The filters live in the URL (q, status, page); typing waits 300 ms before
// it changes the URL and goes back to page 1. Every guardian.view holder
// sees the page (there is no family.view); the API decides which families:
// a guardian sees only the family of their own profile.

/** How long typing must pause before the search runs. */
export const SEARCH_DEBOUNCE_MS = 300;

export function FamiliesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('families.title'));
  const canManage = useCan('family.manage');
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    status: `${id}-status`,
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const params = listParams(searchParams);
  const [text, setText] = useState(params.q);
  const [shownQ, setShownQ] = useState(params.q);
  if (params.q !== shownQ) {
    // Back/forward or a link changed q: show it in the field.
    setShownQ(params.q);
    if (text.trim() !== params.q) {
      setText(params.q);
    }
  }

  /** Sets one filter (empty removes it) and goes back to page 1. */
  function setFilter(
    name: 'q' | 'status',
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

  const list = useQuery({
    queryKey: familiesListKey(params),
    queryFn: ({ signal }) => fetchFamilies(params, signal),
    placeholderData: keepPreviousData,
  });

  const tableState: FamiliesTableState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('families.title')}
        </h1>
        {canManage ? (
          <Link to="/families/new" className={LINK_BUTTON_CLASS}>
            {t('families.add')}
          </Link>
        ) : null}
      </div>
      <div
        role="search"
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('families.list.search')}
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
          <label htmlFor={ids.status} className="text-sm font-medium">
            {t('families.list.status')}
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
            <option value="active">{t('families.status.active')}</option>
            <option value="inactive">{t('families.status.inactive')}</option>
            <option value="all">{t('families.list.statusAll')}</option>
          </select>
        </div>
      </div>
      <FamiliesTable
        state={tableState}
        labelledBy={ids.title}
        searching={params.q !== ''}
        onPage={goToPage}
      />
    </div>
  );
}
