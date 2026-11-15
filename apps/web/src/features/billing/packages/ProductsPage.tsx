import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { todayInZone } from '@/lib/time';
import { fetchSubjectOptions, PRICE_OPTIONS_KEY } from '../pricing/api';
import {
  createProduct,
  fetchProducts,
  PRODUCTS_QUERY_KEY,
  productsListKey,
  type ProductListFilters,
} from './api';
import { ProductDialog } from './ProductDialog';
import { ProductsView, type ProductsState } from './ProductsView';
import {
  productCreateBody,
  productListFilters,
  productListParams,
} from './rules';

// /billing/package-products (#377 B1, B2), for billing.manage holders: the
// products with the status and subject filters and the page in the URL,
// and "New package" (a stored product opens its page). The subjects load
// with the page (the filter and the dialog share them).

export function ProductsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.packages.products.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const timeZone = useCenterTimeZone();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(
    () => productListFilters(searchParams),
    [searchParams],
  );
  const [adding, setAdding] = useState(false);

  const list = useQuery({
    queryKey: productsListKey(filters),
    queryFn: ({ signal }) => fetchProducts(filters, signal),
    placeholderData: keepPreviousData,
  });
  const subjects = useQuery({
    queryKey: [...PRICE_OPTIONS_KEY, 'subjects'],
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
  });

  const params = productListParams(filters).toString();
  const listSearch = params ? `?${params}` : '';

  function apply(next: ProductListFilters) {
    setSearchParams(productListParams(next));
  }

  const state: ProductsState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <>
      <ProductsView
        state={state}
        filters={filters}
        subjects={subjects.data ?? null}
        onFilters={apply}
        onPage={(page) => apply({ ...filters, page })}
        onAdd={() => setAdding(true)}
        listSearch={listSearch}
      />
      <ProductDialog
        open={adding}
        today={todayInZone(timeZone)}
        subjects={subjects.data ?? null}
        subjectsError={subjects.error ?? undefined}
        onRetrySubjects={() => void subjects.refetch()}
        onSubmit={async (values) => {
          const created = await createProduct(productCreateBody(values));
          await queryClient.invalidateQueries({
            queryKey: PRODUCTS_QUERY_KEY,
          });
          setAdding(false);
          void navigate(`/billing/package-products/${created.id}`, {
            state: { listSearch },
          });
        }}
        onClose={() => setAdding(false)}
      />
    </>
  );
}
