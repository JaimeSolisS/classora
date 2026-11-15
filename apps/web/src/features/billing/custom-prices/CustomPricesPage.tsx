import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { todayInZone } from '@/lib/time';
import { UNBILLED_QUERY_KEY } from '../unbilled/api';
import { fetchTarget, targetKey, type TargetOption } from '../targets';
import {
  CATALOG_OPTIONS_KEY,
  createCustomPrice,
  CUSTOM_PRICES_QUERY_KEY,
  customPricesListKey,
  deleteCustomPrice,
  endCustomPrice,
  fetchCatalogOptions,
  fetchCustomPrices,
  type CustomPrice,
  type CustomPriceListFilters,
} from './api';
import { CustomPriceDialog } from './CustomPriceDialog';
import { CustomPricesView, type CustomPricesState } from './CustomPricesView';
import { EndCustomPriceDialog } from './EndCustomPriceDialog';
import { useCustomPriceLabels } from './labels';
import {
  customPriceCreateBody,
  customPriceListFilters,
  customPriceListParams,
} from './rules';

// /billing/custom-prices (#367 A1, E), for billing.manage holders: the
// list with the student or family filter, the status and the page in the
// URL (a deep link ?student_id= or ?family_id= preselects the target, also
// in "Add custom price"), the add and end dialogs and "Delete" behind a
// confirmation (409 custom_price_in_effect/in_use keep it open with
// "Reload"). After a change the custom price queries and the unbilled
// list (a custom price can make a session billable) are invalidated.

export function CustomPricesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.customPrices.title'));
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone);
  const labels = useCustomPriceLabels();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(
    () => customPriceListFilters(searchParams),
    [searchParams],
  );
  const [picked, setPicked] = useState<TargetOption | null>(null);
  const [adding, setAdding] = useState(false);
  const [ending, setEnding] = useState<CustomPrice | null>(null);
  const [deleting, setDeleting] = useState<CustomPrice | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');

  const list = useQuery({
    queryKey: customPricesListKey(filters),
    queryFn: ({ signal }) => fetchCustomPrices(filters, signal),
    placeholderData: keepPreviousData,
  });
  const target = filters.target;
  // The filtered target's name: the one just picked, else read by id (a
  // deep link).
  const knownTarget =
    picked && target && picked.id === target.id && picked.kind === target.kind
      ? picked
      : null;
  const targetRead = useQuery({
    queryKey: targetKey(target?.kind ?? 'student', target?.id ?? ''),
    queryFn: ({ signal }) => fetchTarget(target!.kind, target!.id, signal),
    enabled: target !== null && knownTarget === null,
    retry: false,
  });
  const filterTarget: TargetOption | null = target
    ? (knownTarget ?? targetRead.data ?? null)
    : null;
  const catalog = useQuery({
    queryKey: CATALOG_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchCatalogOptions(signal),
    enabled: adding,
  });

  function apply(next: CustomPriceListFilters) {
    setSearchParams(customPriceListParams(next));
  }

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: CUSTOM_PRICES_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: UNBILLED_QUERY_KEY }),
    ]);
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteError(null);
    try {
      await deleteCustomPrice(deleting.id);
      setDeleting(null);
      setAnnouncement(t('billing.customPrices.deleted'));
      await refresh();
    } catch (caught) {
      setDeleteError(caught);
    }
  }

  const refused =
    deleteError instanceof ApiError &&
    (deleteError.code === 'custom_price_in_effect' ||
      deleteError.code === 'custom_price_in_use');

  const state: CustomPricesState = list.data
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
      <CustomPricesView
        state={state}
        filters={filters}
        targetName={filterTarget?.name ?? null}
        onTarget={(next) => {
          setPicked(next);
          apply({
            ...filters,
            target: next ? { kind: next.kind, id: next.id } : null,
            page: 1,
          });
        }}
        onFilters={apply}
        onPage={(page) => apply({ ...filters, page })}
        onAdd={() => {
          setAnnouncement('');
          setAdding(true);
        }}
        onEnd={(item) => {
          setAnnouncement('');
          setEnding(item);
        }}
        onDelete={(item) => {
          setAnnouncement('');
          setDeleteError(null);
          setDeleting(item);
        }}
        today={today}
        announcement={announcement}
      />
      <CustomPriceDialog
        open={adding}
        today={today}
        initialTarget={filterTarget}
        catalog={catalog.data ?? null}
        catalogError={catalog.error ?? undefined}
        onRetryCatalog={() => void catalog.refetch()}
        onSubmit={async (values) => {
          await createCustomPrice(customPriceCreateBody(values));
          await refresh();
          setAdding(false);
          setAnnouncement(t('billing.customPrices.added'));
        }}
        onClose={() => setAdding(false)}
      />
      <EndCustomPriceDialog
        item={ending}
        today={today}
        onSubmit={async (item, date) => {
          await endCustomPrice(item.id, {
            version: item.version,
            expires_on: date,
          });
          await refresh();
          setEnding(null);
          setAnnouncement(t('billing.customPrices.ended'));
        }}
        onReload={() => {
          setEnding(null);
          void refresh();
        }}
        onClose={() => setEnding(null)}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={t('billing.customPrices.deleteDialog.title')}
        body={
          deleting
            ? t('billing.customPrices.deleteDialog.body', {
                name: labels.target(deleting).name,
                service: labels.service(deleting.service_price),
              })
            : ''
        }
        confirmLabel={t('billing.customPrices.delete')}
        destructive
        alert={
          deleteError ? (
            <ErrorAlert
              error={deleteError}
              onRetry={
                refused
                  ? () => {
                      setDeleting(null);
                      setDeleteError(null);
                      void refresh();
                    }
                  : undefined
              }
              retryLabel={refused ? t('billing.prices.reload') : undefined}
            />
          ) : null
        }
        onConfirm={confirmDelete}
        onCancel={() => {
          setDeleting(null);
          setDeleteError(null);
        }}
      />
    </>
  );
}
