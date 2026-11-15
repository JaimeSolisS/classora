import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import {
  fetchLevelOptions,
  fetchSubjectOptions,
  PRICE_OPTIONS_KEY,
} from '../pricing/api';
import {
  createDiscount,
  DISCOUNTS_QUERY_KEY,
  discountsListKey,
  fetchDiscounts,
  type DiscountListFilters,
} from './api';
import { DiscountDialog } from './DiscountDialog';
import { DiscountsView, type DiscountsState } from './DiscountsView';
import {
  discountCreateBody,
  discountListFilters,
  discountListParams,
} from './rules';

// /billing/discounts (#367 A1, B, C), for billing.manage holders: the list
// with its filters and page in the URL (?kind=&status=&q=&page=, so a
// reload and Back keep them), and "Add discount". The dialog's subjects
// and levels load when it opens; after a 201 the discount queries are
// invalidated and the page opens the new discount.

export function DiscountsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.discounts.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(
    () => discountListFilters(searchParams),
    [searchParams],
  );
  const [adding, setAdding] = useState(false);

  const list = useQuery({
    queryKey: discountsListKey(filters),
    queryFn: ({ signal }) => fetchDiscounts(filters, signal),
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

  const apply = useCallback(
    (next: DiscountListFilters) => {
      setSearchParams(discountListParams(next), {
        replace: next.q !== filters.q,
      });
    },
    [setSearchParams, filters.q],
  );

  const state: DiscountsState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };
  const search = discountListParams(filters).toString();
  const listSearch = search ? `?${search}` : '';

  return (
    <>
      <DiscountsView
        state={state}
        filters={filters}
        onFilters={apply}
        onPage={(page) => apply({ ...filters, page })}
        onAdd={() => setAdding(true)}
        listSearch={listSearch}
      />
      <DiscountDialog
        open={adding}
        subjects={subjects.data ?? null}
        levels={levels.data ?? null}
        optionsError={subjects.error ?? levels.error ?? undefined}
        onRetryOptions={() => {
          void subjects.refetch();
          void levels.refetch();
        }}
        onSubmit={async (values) => {
          const created = await createDiscount(discountCreateBody(values));
          await queryClient.invalidateQueries({
            queryKey: DISCOUNTS_QUERY_KEY,
          });
          setAdding(false);
          void navigate(`/billing/discounts/${created.id}`, {
            state: { listSearch },
          });
        }}
        onClose={() => setAdding(false)}
      />
    </>
  );
}
