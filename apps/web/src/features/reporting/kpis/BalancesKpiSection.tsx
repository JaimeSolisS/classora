import { useTranslation } from 'react-i18next';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import type { Balances } from '../api';
import { BalanceCards, type BalanceLinks } from '../BalancesSection';
import { Money } from '../parts';
import { isZeroAmount } from './format';
import { KpiSection, NOTE_CLASS } from './KpiSection';
import { useKpiBalances } from './queries';
import { kpiState, type KpiState } from './state';

// K3 "Unpaid balances and payroll" (M8-09b C2; _docs/kpis.md K3): the
// financial report's balances as of today, with unpaid payroll first and
// prominent. It takes the page's range for the widget contract (#127) but
// ignores it: the balances are as of today only (#126 E10).

const L = 'reports.kpis.balances';

function isEmptyBalances({
  receivables,
  payables,
  deferred_revenue: deferred,
}: Balances): boolean {
  return (
    receivables.accounts_with_outstanding === 0 &&
    payables.payroll_periods_unpaid === 0 &&
    [
      receivables.outstanding,
      receivables.overdue,
      receivables.account_credit,
      payables.expenses_open,
      payables.expenses_overdue,
      payables.payroll_unpaid,
      payables.total,
      deferred.total,
    ].every(isZeroAmount)
  );
}

export interface BalancesKpiViewProps {
  state: KpiState<Balances>;
  links: BalanceLinks;
}

export function BalancesKpiView({ state, links }: BalancesKpiViewProps) {
  const { t } = useTranslation();
  return (
    <KpiSection
      kpi="balances"
      title={t(`${L}.title`)}
      subtitle={t(`${L}.note`)}
      state={state}
      isEmpty={isEmptyBalances}
      rows={3}
    >
      {(balances) => (
        <div className="flex min-w-0 flex-col gap-4">
          <div
            className="flex max-w-xl min-w-0 flex-col gap-1 rounded-lg border-2 border-foreground p-4"
            data-kpi-payroll-unpaid
          >
            <p className="font-medium">{t(`${L}.payrollUnpaid`)}</p>
            <Money
              amount={balances.payables.payroll_unpaid}
              className="justify-start text-2xl font-semibold"
            />
            <p className={NOTE_CLASS}>
              {t('reports.balances.payables.payrollPeriods', {
                count: balances.payables.payroll_periods_unpaid,
              })}
            </p>
          </div>
          <BalanceCards balances={balances} links={links} />
        </div>
      )}
    </KpiSection>
  );
}

export interface BalancesKpiSectionProps {
  /** Ignored: the balances are as of today (the widget contract, #127). */
  from?: string;
  to?: string;
}

/** K3 with its own query (#127 mounts it as a widget). */
export function BalancesKpiSection(props: BalancesKpiSectionProps) {
  void props;
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const query = useKpiBalances();
  return (
    <BalancesKpiView
      state={kpiState(query)}
      links={{
        overdueAccounts: canAccess(user, { permission: 'billing.view' }),
        overdueExpenses: canAccess(user, { permission: 'expense.view' }),
      }}
    />
  );
}
