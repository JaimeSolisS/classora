import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState, type ReactNode } from 'react';
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
import type { PriceVersion } from '../pricing/api';
import { usePriceLabels } from '../pricing/labels';
import { PriceVersionDialog } from '../pricing/PriceVersionDialog';
import { PriceVersionsTable } from '../pricing/PriceVersionsTable';
import { versionCreateBody, type VersionFormValues } from '../pricing/rules';
import {
  createProductVersion,
  deleteProductVersion,
  fetchProduct,
  productKey,
  PRODUCTS_QUERY_KEY,
  updateProduct,
  type PackageProductDetail,
} from './api';
import { usePackageLabels } from './labels';
import { ProductActiveBadge } from './PackageBadges';
import { PackagesNav } from './PackagesNav';
import { ProductNameDialog } from './ProductNameDialog';
import { isVersionConflict } from './rules';

// /billing/package-products/:productId (#377 B3), for billing.manage
// holders: the product as a definition list with the note that its unit,
// quantity, validity and subject are fixed (#94 L7), "Edit" (the name),
// "Deactivate" (behind a confirmation) or "Activate", and its price
// versions with "Schedule a price change" and "Delete" on upcoming ones
// (the #89b components against the product's version routes). Each PATCH
// sends `version`; a 409 version_conflict shows the shared message and
// reloads the product; 409 price_version_in_use / _in_effect show their
// message with a reload. An unknown id is the not-found message.

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
  return `/billing/package-products${search.startsWith('?') ? search : ''}`;
}

function BackLink() {
  const { t } = useTranslation();
  const to = useBackTo();
  return (
    <p>
      <Link to={to} className={CATALOG_LINK_CLASS}>
        {t('billing.packages.products.back')}
      </Link>
    </p>
  );
}

export function ProductPage() {
  const { t } = useTranslation();
  const { productId = '' } = useParams();
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const detail = useQuery({
    queryKey: productKey(productId),
    queryFn: ({ signal }) => fetchProduct(productId, signal),
  });
  const notFound =
    detail.error instanceof ApiError &&
    (detail.error.status === 404 || detail.error.status === 422);
  useDocumentTitle(
    notFound
      ? t('billing.packages.products.notFound')
      : (detail.data?.name ?? t('billing.packages.products.detailTitle')),
  );

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <div className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">
            {notFound
              ? t('billing.packages.products.notFound')
              : t('billing.packages.products.detailTitle')}
          </h1>
          {notFound ? (
            <p>{t('billing.packages.products.notFoundHelp')}</p>
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

  const product = detail.data;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: PRODUCTS_QUERY_KEY });
  }

  return (
    <ProductPageView
      product={product}
      today={todayInZone(timeZone)}
      onRename={async (name) => {
        await updateProduct(productId, { version: product.version, name });
        await refresh();
      }}
      onSetActive={async (isActive) => {
        await updateProduct(productId, {
          version: product.version,
          is_active: isActive,
        });
        await refresh();
      }}
      onSchedule={async (values) => {
        await createProductVersion(productId, versionCreateBody(values));
        await refresh();
      }}
      onDelete={async (version) => {
        await deleteProductVersion(productId, version.id);
        await refresh();
      }}
      onReload={refresh}
    />
  );
}

export interface ProductPageViewProps {
  product: PackageProductDetail;
  today: LocalDate;
  /** Saves the name; rejects with the error to show. */
  onRename: (name: string) => Promise<void>;
  /** Activates or deactivates; rejects with the error to show. */
  onSetActive: (isActive: boolean) => Promise<void>;
  /** Schedules a price version; rejects with the error to show. */
  onSchedule: (values: VersionFormValues) => Promise<void>;
  /** Deletes an upcoming version; rejects with the error to show. */
  onDelete: (version: PriceVersion) => Promise<void>;
  onReload: () => Promise<void>;
}

/** The product page's content, without any query. */
export function ProductPageView({
  product,
  today,
  onRename,
  onSetActive,
  onSchedule,
  onDelete,
  onReload,
}: ProductPageViewProps) {
  const { t } = useTranslation();
  const labels = usePackageLabels();
  const priceLabels = usePriceLabels();
  const id = useId();
  const ids = { versions: `${id}-versions` };
  const [editing, setEditing] = useState(false);
  const [deactivating, setDeactivating] = useState(false);
  const [toggleError, setToggleError] = useState<unknown>(null);
  const [activating, setActivating] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [deleting, setDeleting] = useState<PriceVersion | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');

  async function setActive(next: boolean) {
    setToggleError(null);
    try {
      await onSetActive(next);
      setDeactivating(false);
      setAnnouncement(
        next
          ? t('billing.packages.product.activated')
          : t('billing.packages.product.deactivated'),
      );
    } catch (caught) {
      setToggleError(caught);
      if (isVersionConflict(caught)) {
        void onReload();
      }
    }
  }

  async function activate() {
    setAnnouncement('');
    setActivating(true);
    await setActive(true);
    setActivating(false);
  }

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

  const terms: [string, ReactNode][] = [
    [t('billing.packages.products.columns.units'), labels.units(product)],
    [t('billing.packages.products.columns.validity'), labels.validity(product)],
    [t('billing.packages.products.columns.subject'), labels.subject(product)],
    [t('billing.packages.products.columns.price'), labels.price(product)],
    [
      t('billing.packages.products.columns.status'),
      <ProductActiveBadge key="status" active={product.is_active} />,
    ],
  ];

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <PackagesNav />
      <div className="flex flex-col gap-3">
        <BackLink />
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold break-words">{product.name}</h1>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              onClick={() => {
                setAnnouncement('');
                setEditing(true);
              }}
            >
              {t('billing.packages.product.edit')}
            </Button>
            {product.is_active ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setAnnouncement('');
                  setToggleError(null);
                  setDeactivating(true);
                }}
              >
                {t('billing.packages.product.deactivate')}
              </Button>
            ) : (
              <Button
                type="button"
                variant="outline"
                disabled={activating}
                onClick={() => void activate()}
              >
                {t('billing.packages.product.activate')}
              </Button>
            )}
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.packages.products.fixedHint')}
        </p>
        <p role="status" className="text-sm">
          {announcement}
        </p>
        {toggleError && !deactivating ? (
          <ErrorAlert error={toggleError} />
        ) : null}
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
        <PriceVersionsTable
          versions={product.versions}
          labelledBy={ids.versions}
          onDelete={(version) => {
            setAnnouncement('');
            setDeleteError(null);
            setDeleting(version);
          }}
        />
      </section>
      <ProductNameDialog
        open={editing}
        name={product.name}
        onSubmit={onRename}
        onStale={() => void onReload()}
        onClose={(saved) => {
          setEditing(false);
          if (saved) {
            setAnnouncement(t('billing.packages.product.saved'));
          }
        }}
      />
      <PriceVersionDialog
        open={scheduling}
        versions={product.versions}
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
        open={deactivating}
        title={t('billing.packages.product.deactivateDialog.title')}
        body={t('billing.packages.product.deactivateDialog.body', {
          name: product.name,
        })}
        confirmLabel={t('billing.packages.product.deactivate')}
        destructive
        alert={toggleError ? <ErrorAlert error={toggleError} /> : null}
        onConfirm={() => setActive(false)}
        onCancel={() => {
          setDeactivating(false);
          setToggleError(null);
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={t('billing.prices.deleteDialog.title')}
        body={t('billing.prices.deleteDialog.body', {
          date: deleting ? priceLabels.date(deleting) : '',
          amount: deleting ? priceLabels.amount(deleting) : '',
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
