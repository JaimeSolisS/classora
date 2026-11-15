import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
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
import { Pagination } from '@/features/catalog/Pagination';
import { FamilyStatusBadge } from '@/features/families/FamilyBadges';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { BillingTabs } from '../BillingTabs';
import {
  BUCKETS,
  MIN_DAYS_OPTIONS,
  type AgingBuckets,
  type MinDaysOverdue,
  type OverdueAccount,
  type OverdueFamilyStatus,
  type OverdueFilters,
  type OverduePage,
} from './api';
import { overdueAccountPath } from './rules';

// The overdue accounts (#382 B), without any query (stories render each
// state): the Billing tabs, the title, the filters (kept in the URL by the
// page), the totals as stat tiles (plain text, no chart), then one row per
// account, each linking to the family's account filtered to its overdue
// charges. From `lg` a table; below it each account is a card (family,
// overdue total, days overdue, oldest due date) with the aging buckets
// collapsed, so nothing scrolls sideways at 320 px. Account credit is
// shown, not subtracted (#97).

const LINK_CLASS =
  'inline-block min-h-6 font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export type OverdueState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: OverduePage };

export interface OverdueViewProps {
  state: OverdueState;
  filters: OverdueFilters;
  onFilters: (filters: OverdueFilters) => void;
}

function FamilyName({ account }: { account: OverdueAccount }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Link to={overdueAccountPath(account.family.id)} className={LINK_CLASS}>
        {account.family.name}
      </Link>
      {account.family.is_active ? null : <FamilyStatusBadge active={false} />}
    </span>
  );
}

export function OverdueView({ state, filters, onFilters }: OverdueViewProps) {
  const { t } = useTranslation();
  const { formatMoney, formatLocalDate, formatInstant } = useFormatters();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    totals: `${id}-totals`,
    minDays: `${id}-min-days`,
    status: `${id}-status`,
  };
  const bucketLabel = (bucket: keyof AgingBuckets) =>
    t(`billing.overdue.buckets.${bucket}`);
  const days = (count: number) => t('billing.overdue.days', { count });
  const lastReminder = (account: OverdueAccount) =>
    account.last_reminded_at
      ? formatInstant(account.last_reminded_at, 'date')
      : '—';

  let totals = null;
  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { page } = state;
    const tiles: [string, string][] = [
      [t('billing.overdue.totals.accounts'), String(page.totals.accounts)],
      [
        t('billing.overdue.totals.overdueTotal'),
        formatMoney(page.totals.overdue_total),
      ],
      ...BUCKETS.map((bucket): [string, string] => [
        bucketLabel(bucket),
        formatMoney(page.totals.buckets[bucket]),
      ]),
    ];
    totals = (
      <section aria-labelledby={ids.totals} className="flex flex-col gap-3">
        <h2 id={ids.totals} className="text-lg font-semibold">
          {t('billing.overdue.totals.title')}
        </h2>
        <dl className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
          {tiles.map(([term, value]) => (
            <div
              key={term}
              data-overdue-total
              className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3"
            >
              <dt className="text-sm text-muted-foreground">{term}</dt>
              <dd className="text-lg font-semibold break-words">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="text-sm text-muted-foreground">
          {t('billing.overdue.totals.asOf', {
            date: formatLocalDate(page.today),
          })}
        </p>
      </section>
    );
    if (page.items.length === 0) {
      body = <p>{t('billing.overdue.empty')}</p>;
    } else {
      body = (
        <div className="flex flex-col gap-4">
          <div className="hidden lg:block">
            <Table aria-labelledby={ids.title}>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.family')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.overdueTotal')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.overdueCharges')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.oldestDueDate')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.daysOverdue')}
                  </TableHead>
                  {BUCKETS.map((bucket) => (
                    <TableHead key={bucket} scope="col">
                      {bucketLabel(bucket)}
                    </TableHead>
                  ))}
                  <TableHead scope="col">
                    {t('billing.overdue.columns.outstanding')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.accountCredit')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.overdue.columns.lastReminder')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((account) => (
                  <TableRow
                    key={account.account_id}
                    data-overdue-account={account.family.id}
                  >
                    <TableCell className="max-w-56 whitespace-normal break-words">
                      <FamilyName account={account} />
                    </TableCell>
                    <TableCell>{formatMoney(account.overdue_total)}</TableCell>
                    <TableCell>{account.overdue_charges}</TableCell>
                    <TableCell>
                      {formatLocalDate(account.oldest_due_date)}
                    </TableCell>
                    <TableCell>{account.days_overdue}</TableCell>
                    {BUCKETS.map((bucket) => (
                      <TableCell key={bucket}>
                        {formatMoney(account.buckets[bucket])}
                      </TableCell>
                    ))}
                    <TableCell>{formatMoney(account.outstanding)}</TableCell>
                    <TableCell>{formatMoney(account.account_credit)}</TableCell>
                    <TableCell>{lastReminder(account)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul
            className="flex flex-col gap-3 lg:hidden"
            aria-labelledby={ids.title}
          >
            {page.items.map((account) => (
              <li
                key={account.account_id}
                data-overdue-card={account.family.id}
                className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-3"
              >
                <FamilyName account={account} />
                <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm min-[360px]:grid-cols-2">
                  {(
                    [
                      [
                        t('billing.overdue.columns.overdueTotal'),
                        formatMoney(account.overdue_total),
                      ],
                      [
                        t('billing.overdue.columns.daysOverdue'),
                        days(account.days_overdue),
                      ],
                      [
                        t('billing.overdue.columns.oldestDueDate'),
                        formatLocalDate(account.oldest_due_date),
                      ],
                    ] as const
                  ).map(([term, value]) => (
                    <div key={term} className="flex min-w-0 flex-col">
                      <dt className="text-muted-foreground">{term}</dt>
                      <dd className="font-medium break-words">{value}</dd>
                    </div>
                  ))}
                </dl>
                <details className="text-sm">
                  <summary className="min-h-6 cursor-pointer rounded-md font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring">
                    {t('billing.overdue.aging')}
                  </summary>
                  <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 min-[360px]:grid-cols-2">
                    {(
                      [
                        ...BUCKETS.map((bucket): [string, string] => [
                          bucketLabel(bucket),
                          formatMoney(account.buckets[bucket]),
                        ]),
                        [
                          t('billing.overdue.columns.overdueCharges'),
                          String(account.overdue_charges),
                        ],
                        [
                          t('billing.overdue.columns.outstanding'),
                          formatMoney(account.outstanding),
                        ],
                        [
                          t('billing.overdue.columns.accountCredit'),
                          formatMoney(account.account_credit),
                        ],
                        [
                          t('billing.overdue.columns.lastReminder'),
                          lastReminder(account),
                        ],
                      ] as [string, string][]
                    ).map(([term, value]) => (
                      <div key={term} className="flex min-w-0 flex-col">
                        <dt className="text-muted-foreground">{term}</dt>
                        <dd className="break-words">{value}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              </li>
            ))}
          </ul>
          <Pagination
            offset={page.offset}
            limit={page.limit}
            shown={page.items.length}
            total={page.total}
            onPage={(next) => onFilters({ ...filters, page: next })}
            label={t('billing.overdue.pagination')}
          />
        </div>
      );
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-2">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('billing.overdue.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('billing.overdue.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.overdue.filters.label')}
        className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:max-w-2xl"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.minDays} className="text-sm font-medium">
            {t('billing.overdue.filters.minDays')}
          </label>
          <select
            id={ids.minDays}
            className={SELECT_CLASS}
            value={filters.minDays}
            onChange={(event) =>
              onFilters({
                ...filters,
                minDays: Number(event.target.value) as MinDaysOverdue,
                page: 1,
              })
            }
          >
            {MIN_DAYS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {t(`billing.overdue.filters.minDaysOptions.d${option}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.status} className="text-sm font-medium">
            {t('billing.overdue.filters.status')}
          </label>
          <select
            id={ids.status}
            className={SELECT_CLASS}
            value={filters.familyStatus}
            onChange={(event) =>
              onFilters({
                ...filters,
                familyStatus: event.target.value as OverdueFamilyStatus,
                page: 1,
              })
            }
          >
            <option value="all">{t('families.list.statusAll')}</option>
            <option value="active">{t('families.status.active')}</option>
            <option value="inactive">{t('families.status.inactive')}</option>
          </select>
        </div>
      </div>
      {totals}
      {body}
    </div>
  );
}
