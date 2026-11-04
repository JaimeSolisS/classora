import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useFormatters } from '@/lib/formatters';
import type { Balances } from './api';
import { FigureRow, ReportStatus, type ReportState } from './parts';

// Today's balances (M7-14b C), without any query: receivables, payables and
// deferred revenue, each card stating the API's `today`. A link shows only
// when the viewer may open its target (C2); the payroll periods list has no
// "unpaid" filter, so unpaid payroll has no link.

const L = 'reports.balances';
const LINK_CLASS =
  'inline-flex min-h-11 items-center text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

export interface BalanceLinks {
  /** `/billing/overdue` (billing.view). */
  overdueAccounts: boolean;
  /** `/expenses/records?status=overdue` (expense.view). */
  overdueExpenses: boolean;
}

function Card({
  title,
  today,
  data,
  children,
  footer,
}: {
  title: string;
  today: string;
  data: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
      data-balance-card={data}
    >
      <div className="flex flex-col">
        <h3 id={titleId} className="text-lg font-semibold">
          {title}
        </h3>
        <p className="text-sm text-muted-foreground" data-balance-as-of>
          {t(`${L}.asOf`, { date: formatLocalDate(today) })}
        </p>
      </div>
      <dl className="flex min-w-0 flex-col">{children}</dl>
      {footer}
    </section>
  );
}

export function BalanceCards({
  balances,
  links,
}: {
  balances: Balances;
  links: BalanceLinks;
}) {
  const { t } = useTranslation();
  const { today, receivables, payables, deferred_revenue: deferred } = balances;
  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-3">
      <Card
        title={t(`${L}.receivables.title`)}
        today={today}
        data="receivables"
        footer={
          links.overdueAccounts ? (
            <Link
              to="/billing/overdue"
              className={LINK_CLASS}
              data-balance-link="receivables"
            >
              {t(`${L}.receivables.link`)}
            </Link>
          ) : null
        }
      >
        <FigureRow
          label={t(`${L}.receivables.outstanding`)}
          amount={receivables.outstanding}
          data="outstanding"
        />
        <FigureRow
          label={t(`${L}.receivables.overdue`)}
          amount={receivables.overdue}
          data="overdue"
        />
        <FigureRow
          label={t(`${L}.receivables.credit`)}
          amount={receivables.account_credit}
          data="credit"
        />
        <div
          className="flex flex-wrap items-baseline justify-between gap-x-4 py-1"
          data-figure="accounts"
        >
          <dt>{t(`${L}.receivables.accounts`)}</dt>
          <dd className="tabular-nums">
            {receivables.accounts_with_outstanding}
          </dd>
        </div>
      </Card>
      <Card
        title={t(`${L}.payables.title`)}
        today={today}
        data="payables"
        footer={
          links.overdueExpenses ? (
            <Link
              to="/expenses/records?status=overdue"
              className={LINK_CLASS}
              data-balance-link="payables"
            >
              {t(`${L}.payables.link`)}
            </Link>
          ) : null
        }
      >
        <FigureRow
          label={t(`${L}.payables.expensesOpen`)}
          amount={payables.expenses_open}
          data="expenses-open"
        />
        <FigureRow
          label={t(`${L}.payables.expensesOverdue`)}
          amount={payables.expenses_overdue}
          data="expenses-overdue"
        />
        <FigureRow
          label={t(`${L}.payables.payrollUnpaid`)}
          detail={`(${t(`${L}.payables.payrollPeriods`, {
            count: payables.payroll_periods_unpaid,
          })})`}
          amount={payables.payroll_unpaid}
          data="payroll-unpaid"
        />
        <FigureRow
          label={t(`${L}.payables.total`)}
          amount={payables.total}
          strong
          data="payables-total"
        />
      </Card>
      <Card title={t(`${L}.deferred.title`)} today={today} data="deferred">
        <FigureRow
          label={t(`${L}.deferred.packages`)}
          amount={deferred.packages}
          data="packages"
        />
        <FigureRow
          label={t(`${L}.deferred.courses`)}
          amount={deferred.courses}
          data="courses"
        />
        <FigureRow
          label={t(`${L}.deferred.scheduledSessions`)}
          amount={deferred.scheduled_sessions}
          data="scheduled-sessions"
        />
        <FigureRow
          label={t(`${L}.deferred.other`)}
          amount={deferred.other}
          data="other"
        />
        <FigureRow
          label={t(`${L}.deferred.total`)}
          amount={deferred.total}
          strong
          data="deferred-total"
        />
      </Card>
    </div>
  );
}

export function BalancesView({
  state,
  links,
}: {
  state: ReportState<Balances>;
  links: BalanceLinks;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-4" data-report-balances>
      <ReportStatus state={state} label={t('feedback.loading')} rows={3}>
        {(balances) => <BalanceCards balances={balances} links={links} />}
      </ReportStatus>
    </div>
  );
}
