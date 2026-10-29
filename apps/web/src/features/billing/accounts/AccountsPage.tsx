import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { familiesListKey, fetchFamilies } from '@/features/families/api';
import { listParams } from '@/features/families/rules';
import { AccountsView, type AccountsState } from './AccountsView';

// /billing/accounts (#379 B1), for billing.view holders (every one of them
// also holds guardian.view): the family search on GET /api/v1/families,
// as the families page does it: q, status and page in the URL; typing
// waits 300 ms before it changes the URL and goes back to page 1.

/** How long typing must pause before the search runs (as /families). */
export const SEARCH_DEBOUNCE_MS = 300;

export function AccountsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.accounts.list.title'));
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

  const list = useQuery({
    queryKey: familiesListKey(params),
    queryFn: ({ signal }) => fetchFamilies(params, signal),
    placeholderData: keepPreviousData,
  });

  const state: AccountsState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <AccountsView
      state={state}
      text={text}
      onText={setText}
      status={params.status}
      onStatus={(status) =>
        setFilter('status', status === 'active' ? '' : status)
      }
      searching={params.q !== ''}
      onPage={(page) =>
        setSearchParams((previous) => {
          const next = new URLSearchParams(previous);
          if (page > 1) {
            next.set('page', String(page));
          } else {
            next.delete('page');
          }
          return next;
        })
      }
    />
  );
}
