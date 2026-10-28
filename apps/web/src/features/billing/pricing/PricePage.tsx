import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { todayInZone, type LocalDate } from '@/lib/time';
import { BillingTabs } from '../BillingTabs';
import {
  createPriceVersion,
  deletePriceVersion,
  fetchPrice,
  priceKey,
  PRICES_QUERY_KEY,
  type PriceVersion,
  type ServicePriceDetail,
} from './api';
import { usePriceLabels } from './labels';
import { PriceVersionDialog } from './PriceVersionDialog';
import { PriceVersionsTable } from './PriceVersionsTable';
import { versionCreateBody, type VersionFormValues } from './rules';

// /billing/prices/:priceId (#363 D), for billing.manage holders: the item's
// key as a definition list, its versions (newest first) with "Delete" on
// upcoming ones behind a confirmation, and "Schedule a price change". After
// a change the price queries (this item and the list) are invalidated. An
// unknown id is the not-found message with a link back; "Back to prices"
// keeps the list's filters and page.

function useBackTo(): string {
  const location = useLocation();
  const state: unknown = location.state;
  const search =
    typeof state === 'object' &&
    state !== null &&
    'listSearch' in state &&
    typeof state.listSearch === 'string'
      ? state.listSearch
      : '';
  return `/billing/prices${search.startsWith('?') ? search : ''}`;
}

function BackLink() {
  const { t } = useTranslation();
  const to = useBackTo();
  return (
    <p>
      <Link to={to} className={CATALOG_LINK_CLASS}>
        {t('billing.prices.back')}
      </Link>
    </p>
  );
}

export function PricePage() {
  const { t } = useTranslation();
  const { priceId = '' } = useParams();
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const detail = useQuery({
    queryKey: priceKey(priceId),
    queryFn: ({ signal }) => fetchPrice(priceId, signal),
  });
  const notFound =
    detail.error instanceof ApiError && detail.error.status === 404;
  useDocumentTitle(
    notFound ? t('billing.prices.notFound') : t('billing.prices.detailTitle'),
  );

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <div className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">
            {notFound
              ? t('billing.prices.notFound')
              : t('billing.prices.detailTitle')}
          </h1>
          {notFound ? (
            <p>{t('billing.prices.notFoundHelp')}</p>
          ) : (
            <ErrorAlert
              error={detail.error}
              onRetry={() => void detail.refetch()}
            />
          )}
          <BackLink />
        </div>
      </div>
    );
  }
  if (!detail.data) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <LoadingState />
      </div>
    );
  }

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: PRICES_QUERY_KEY });
  }

  return (
    <PricePageView
      item={detail.data}
      today={todayInZone(timeZone)}
      onSchedule={async (values) => {
        await createPriceVersion(priceId, versionCreateBody(values));
        await refresh();
      }}
      onDelete={async (version) => {
        await deletePriceVersion(priceId, version.id);
        await refresh();
      }}
      onReload={refresh}
    />
  );
}

interface PricePageViewProps {
  item: ServicePriceDetail;
  today: LocalDate;
  /** Schedules a version; rejects with the error to show. */
  onSchedule: (values: VersionFormValues) => Promise<void>;
  /** Deletes an upcoming version; rejects with the error to show. */
  onDelete: (version: PriceVersion) => Promise<void>;
  onReload: () => Promise<void>;
}

/** The detail page's content, without any query. */
export function PricePageView({
  item,
  today,
  onSchedule,
  onDelete,
  onReload,
}: PricePageViewProps) {
  const { t } = useTranslation();
  const labels = usePriceLabels();
  const id = useId();
  const ids = { versions: `${id}-versions` };
  const [scheduling, setScheduling] = useState(false);
  const [deleting, setDeleting] = useState<PriceVersion | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');

  async function confirmDelete() {
    if (!deleting) {
      return;
    }
    setDeleteError(null);
    try {
      await onDelete(deleting);
      setDeleting(null);
      setAnnouncement(t('billing.prices.versions.deleted'));
    } catch (caught) {
      setDeleteError(caught);
    }
  }

  async function reloadAfterRefusal() {
    setDeleting(null);
    setDeleteError(null);
    await onReload();
  }

  const refused =
    deleteError instanceof ApiError &&
    (deleteError.code === 'price_version_in_effect' ||
      deleteError.code === 'price_version_in_use');

  const terms: [string, string][] = [
    [t('billing.prices.columns.service'), labels.service(item)],
    [t('billing.prices.columns.subject'), labels.subject(item)],
    [t('billing.prices.columns.level'), labels.level(item)],
    [t('billing.prices.columns.duration'), labels.duration(item)],
    [t('billing.prices.columns.scope'), labels.scope(item)],
    [t('billing.prices.columns.current'), labels.current(item)],
    [t('billing.prices.columns.next'), labels.next(item)],
  ];

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-3">
        <BackLink />
        <h1 className="text-2xl font-semibold break-words">
          {t('billing.prices.detailHeading', {
            service: labels.service(item),
            subject: labels.subject(item),
          })}
        </h1>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          {terms.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="font-medium">{term}</dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <section aria-labelledby={ids.versions} className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 id={ids.versions} className="text-lg font-semibold">
            {t('billing.prices.versions.title')}
          </h2>
          <Button
            type="button"
            onClick={() => {
              setAnnouncement('');
              setScheduling(true);
            }}
          >
            {t('billing.prices.schedule.title')}
          </Button>
        </div>
        <p role="status" className="text-sm">
          {announcement}
        </p>
        <PriceVersionsTable
          versions={item.versions}
          labelledBy={ids.versions}
          onDelete={(version) => {
            setAnnouncement('');
            setDeleteError(null);
            setDeleting(version);
          }}
        />
      </section>
      <PriceVersionDialog
        open={scheduling}
        versions={item.versions}
        today={today}
        onSubmit={onSchedule}
        onClose={(saved) => {
          setScheduling(false);
          if (saved) {
            setAnnouncement(t('billing.prices.schedule.saved'));
          }
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={t('billing.prices.deleteDialog.title')}
        body={t('billing.prices.deleteDialog.body', {
          date: deleting ? labels.date(deleting) : '',
          amount: deleting ? labels.amount(deleting) : '',
        })}
        confirmLabel={t('billing.prices.deleteDialog.confirm')}
        destructive
        alert={
          deleteError ? (
            <ErrorAlert
              error={deleteError}
              onRetry={refused ? () => void reloadAfterRefusal() : undefined}
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
    </div>
  );
}
