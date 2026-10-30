import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { fetchOverdueAccounts, overdueKey } from './api';
import { OverdueView, type OverdueState } from './OverdueView';
import { overdueFilters, overdueParams } from './rules';

// /billing/overdue (#382 B), for billing.view holders: the overdue accounts
// with the minimum days overdue, the family status and the page in the URL.

export function OverduePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.overdue.title'));
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => overdueFilters(searchParams), [searchParams]);

  const list = useQuery({
    queryKey: overdueKey(filters),
    queryFn: ({ signal }) => fetchOverdueAccounts(filters, signal),
    placeholderData: keepPreviousData,
  });

  const state: OverdueState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <OverdueView
      state={state}
      filters={filters}
      onFilters={(next) => setSearchParams(overdueParams(next))}
    />
  );
}
