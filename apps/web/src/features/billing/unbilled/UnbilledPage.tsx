import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan } from '@/features/auth/useSession';
import { BILLING_SETTINGS_KEY, fetchBillingSettings } from '../settings/api';
import {
  fetchUnbilledItems,
  unbilledFilters,
  unbilledListKey,
  unbilledParams,
  type UnbilledFilters,
} from './api';
import { UnbilledView, type UnbilledState } from './UnbilledView';

// /billing/unbilled (#366 C), for billing.view holders: the billing start
// date (shared with the Settings tab, for the empty states) and one page of
// the unbilled sessions, with the reason and the page in the URL
// (?reason=&page=, so a reload and Back keep them). A failed load of
// either shows the error with "Try again", which reloads both.

export function UnbilledPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.unbilled.title'));
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = unbilledFilters(searchParams);
  const canOpenFamilies = useCan('guardian.view');
  const canManagePrices = useCan('billing.manage');

  const settings = useQuery({
    queryKey: BILLING_SETTINGS_KEY,
    queryFn: ({ signal }) => fetchBillingSettings(signal),
  });
  const list = useQuery({
    queryKey: unbilledListKey(filters),
    queryFn: ({ signal }) => fetchUnbilledItems(filters, signal),
    placeholderData: keepPreviousData,
  });

  function apply(next: UnbilledFilters) {
    setSearchParams(unbilledParams(next));
  }

  const failed = settings.isError ? settings.error : list.error;
  const state: UnbilledState =
    settings.isError || list.isError
      ? {
          status: 'error',
          error: failed,
          onRetry: () => {
            if (settings.isError) void settings.refetch();
            if (list.isError) void list.refetch();
          },
        }
      : settings.data && list.data
        ? { status: 'ready', settings: settings.data, page: list.data }
        : { status: 'loading' };

  return (
    <UnbilledView
      state={state}
      filters={filters}
      onFilters={apply}
      onPage={(page) => apply({ ...filters, page })}
      canOpenFamilies={canOpenFamilies}
      canManagePrices={canManagePrices}
    />
  );
}
