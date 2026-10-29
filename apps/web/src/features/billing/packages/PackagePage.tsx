import { useQuery } from '@tanstack/react-query';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useCan } from '@/features/auth/useSession';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import { BillingTabs } from '../BillingTabs';
import { ChargeStatusBadge } from '../tuition/TuitionBadges';
import {
  fetchPackage,
  fetchPurchaseCharge,
  packageKey,
  purchaseChargeKey,
  type ChargeItem,
  type PackageDetail,
  type PackageTransaction,
} from './api';
import { usePackageLabels } from './labels';
import { PackageStatusBadge } from './PackageBadges';
import { PackagesNav } from './PackagesNav';

// /billing/packages/:packageId (#377 E), for billing.view holders: the
// package as a definition list (with its purchase charge's amount and
// status, read from the family's charges), the unit history (granted,
// used for a session with a link to the staff session page for
// schedule.view holders, expired, reversal; signed units; date) and the
// rules note. A sale that just opened this page is announced. An unknown
// id is the not-found message with a link back.

function useListSearch(): { search: string; sold: boolean } {
  const location = useLocation();
  const state: unknown = location.state;
  const record =
    typeof state === 'object' && state !== null
      ? (state as Record<string, unknown>)
      : {};
  const search =
    typeof record.listSearch === 'string' && record.listSearch.startsWith('?')
      ? record.listSearch
      : '';
  return { search, sold: record.sold === true };
}

function BackLink() {
  const { t } = useTranslation();
  const { search } = useListSearch();
  return (
    <p>
      <Link to={`/billing/packages${search}`} className={CATALOG_LINK_CLASS}>
        {t('billing.packages.sold.back')}
      </Link>
    </p>
  );
}

export function PackagePage() {
  const { t } = useTranslation();
  const { packageId = '' } = useParams();
  const { sold } = useListSearch();
  const canOpenSessions = useCan('schedule.view');
  const detail = useQuery({
    queryKey: packageKey(packageId),
    queryFn: ({ signal }) => fetchPackage(packageId, signal),
  });
  const pkg = detail.data;
  const charge = useQuery({
    queryKey: purchaseChargeKey(packageId),
    queryFn: ({ signal }) => fetchPurchaseCharge(pkg!, signal),
    enabled: pkg !== undefined && pkg.purchase_charge_id !== null,
    retry: false,
  });
  const notFound =
    detail.error instanceof ApiError &&
    (detail.error.status === 404 || detail.error.status === 422);
  useDocumentTitle(
    notFound
      ? t('billing.packages.sold.notFound')
      : t('billing.packages.sold.detailTitle'),
  );

  if (detail.isError) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <div className="flex flex-col gap-4">
          <h1 className="text-2xl font-semibold">
            {notFound
              ? t('billing.packages.sold.notFound')
              : t('billing.packages.sold.detailTitle')}
          </h1>
          {notFound ? (
            <p>{t('billing.packages.sold.notFoundHelp')}</p>
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
  if (!pkg) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <LoadingState />
      </div>
    );
  }

  return (
    <PackagePageView
      pkg={pkg}
      charge={
        pkg.purchase_charge_id === null
          ? { status: 'none' }
          : charge.data
            ? { status: 'ready', charge: charge.data }
            : charge.isError || charge.data === null
              ? { status: 'unavailable' }
              : { status: 'loading' }
      }
      canOpenSessions={canOpenSessions}
      announcement={sold ? t('billing.packages.sell.done') : ''}
    />
  );
}

export type PurchaseChargeState =
  | { status: 'none' }
  | { status: 'loading' }
  | { status: 'unavailable' }
  | { status: 'ready'; charge: ChargeItem };

export interface PackagePageViewProps {
  pkg: PackageDetail;
  charge: PurchaseChargeState;
  /** schedule.view: a consumption links to the staff session page. */
  canOpenSessions: boolean;
  announcement?: string;
}

/** The package page's content, without any query. */
export function PackagePageView({
  pkg,
  charge,
  canOpenSessions,
  announcement = '',
}: PackagePageViewProps) {
  const { t } = useTranslation();
  const labels = usePackageLabels();
  const id = useId();
  const ids = { history: `${id}-history`, rules: `${id}-rules` };

  let chargeValue: ReactNode;
  if (charge.status === 'ready') {
    chargeValue = (
      <span className="inline-flex flex-wrap items-center gap-2">
        {labels.money(charge.charge.net)}
        <ChargeStatusBadge status={charge.charge.status} />
      </span>
    );
  } else if (charge.status === 'loading') {
    chargeValue = t('billing.packages.package.chargeLoading');
  } else if (charge.status === 'unavailable') {
    chargeValue = t('billing.packages.package.chargeUnavailable');
  } else {
    chargeValue = t('billing.prices.none');
  }

  const terms: [string, ReactNode][] = [
    [t('billing.packages.sold.columns.student'), pkg.student.name],
    [t('billing.packages.sold.columns.family'), pkg.family.name],
    [t('billing.packages.sold.columns.package'), pkg.product.name],
    [t('billing.packages.package.remaining'), labels.remaining(pkg)],
    [t('billing.packages.package.granted'), labels.granted(pkg)],
    [t('billing.packages.products.columns.subject'), labels.subject(pkg)],
    [
      t('billing.packages.sold.columns.purchased'),
      labels.date(pkg.purchased_on),
    ],
    [t('billing.packages.sold.columns.expires'), labels.date(pkg.expires_on)],
    [
      t('billing.packages.sold.columns.status'),
      <PackageStatusBadge key="status" status={pkg.status} />,
    ],
    [t('billing.packages.package.charge'), chargeValue],
  ];

  function kindLabel(movement: PackageTransaction): ReactNode {
    if (movement.kind !== 'consume') {
      return t(`billing.packages.history.kinds.${movement.kind}`);
    }
    const date = movement.session_starts_at
      ? labels.instantDate(movement.session_starts_at)
      : '';
    const text = t('billing.packages.history.kinds.consume', { date });
    return canOpenSessions && movement.class_session_id ? (
      <Link
        to={`/schedule/sessions/${movement.class_session_id}`}
        className={TEXT_LINK_CLASS}
      >
        {text}
      </Link>
    ) : (
      text
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <PackagesNav />
      <div className="flex flex-col gap-3">
        <BackLink />
        <h1 className="text-2xl font-semibold break-words">
          {t('billing.packages.package.heading', {
            name: pkg.product.name,
            student: pkg.student.name,
          })}
        </h1>
        <p role="status" className="text-sm">
          {announcement}
        </p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          {terms.map(([term, value]) => (
            <div key={term} className="contents">
              <dt className="font-medium">{term}</dt>
              <dd className="break-words">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <section aria-labelledby={ids.history} className="flex flex-col gap-4">
        <h2 id={ids.history} className="text-lg font-semibold">
          {t('billing.packages.history.title')}
        </h2>
        {pkg.transactions.length === 0 ? (
          <p>{t('billing.packages.history.empty')}</p>
        ) : (
          <Table aria-labelledby={ids.history}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.packages.history.movement')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.packages.history.units')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.packages.history.date')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pkg.transactions.map((movement) => (
                <TableRow
                  key={movement.id}
                  data-package-transaction={movement.kind}
                >
                  <TableCell className="whitespace-normal">
                    {kindLabel(movement)}
                  </TableCell>
                  <TableCell>{labels.signed(pkg, movement)}</TableCell>
                  <TableCell>
                    {labels.instantDate(movement.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
      <section aria-labelledby={ids.rules} className="flex flex-col gap-2">
        <h2 id={ids.rules} className="text-lg font-semibold">
          {t('billing.packages.rules.title')}
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          <li>{t('billing.packages.rules.consumption')}</li>
          <li>{t('billing.packages.rules.short')}</li>
          <li>{t('billing.packages.rules.expiry')}</li>
          <li>{t('billing.packages.rules.notConverted')}</li>
        </ul>
      </section>
    </div>
  );
}
