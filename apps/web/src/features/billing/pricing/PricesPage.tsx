import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { todayInZone } from '@/lib/time';
import {
  createPrice,
  fetchLevelOptions,
  fetchPrices,
  fetchScopeOptions,
  fetchSubjectOptions,
  PRICE_OPTIONS_KEY,
  PRICES_QUERY_KEY,
  pricesListKey,
  type PriceListFilters,
} from './api';
import { PricesView, type PricesState } from './PricesView';
import { priceCreateBody, priceListFilters, priceListParams } from './rules';
import { ServicePriceDialog } from './ServicePriceDialog';

// /billing/prices (#363 A1, B, C), for billing.manage holders: the list
// with its filters and page in the URL (?class_type=&pricing_model=&page=,
// so a reload and Back keep them), and "Add price". The dialog's subjects
// and levels load when it opens; after a 201 the price queries are
// invalidated and the page opens the new item.

export function PricesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.prices.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const timeZone = useCenterTimeZone();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = priceListFilters(searchParams);
  const [adding, setAdding] = useState(false);

  const list = useQuery({
    queryKey: pricesListKey(filters),
    queryFn: ({ signal }) => fetchPrices(filters, signal),
    placeholderData: keepPreviousData,
  });
  const subjects = useQuery({
    queryKey: [...PRICE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: adding,
  });
  const levels = useQuery({
    queryKey: [...PRICE_OPTIONS_KEY, 'levels'],
    queryFn: ({ signal }) => fetchLevelOptions(signal),
    enabled: adding,
  });

  function apply(next: PriceListFilters) {
    setSearchParams(priceListParams(next));
  }

  const state: PricesState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };
  const listSearch = priceListParams(filters).toString();

  return (
    <>
      <PricesView
        state={state}
        filters={filters}
        onFilters={apply}
        onPage={(page) => apply({ ...filters, page })}
        onAdd={() => setAdding(true)}
        listSearch={listSearch ? `?${listSearch}` : ''}
      />
      <ServicePriceDialog
        open={adding}
        today={todayInZone(timeZone)}
        subjects={subjects.data ?? null}
        levels={levels.data ?? null}
        optionsError={subjects.error ?? levels.error ?? undefined}
        onRetryOptions={() => {
          void subjects.refetch();
          void levels.refetch();
        }}
        fetchScopes={fetchScopeOptions}
        onSubmit={async (values) => {
          const created = await createPrice(priceCreateBody(values));
          await queryClient.invalidateQueries({ queryKey: PRICES_QUERY_KEY });
          setAdding(false);
          void navigate(`/billing/prices/${created.id}`, {
            state: { listSearch: listSearch ? `?${listSearch}` : '' },
          });
        }}
        onClose={() => setAdding(false)}
      />
    </>
  );
}
