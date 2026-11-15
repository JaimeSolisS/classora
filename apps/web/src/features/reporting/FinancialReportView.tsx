import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { Balances, Profitability } from './api';
import { BalancesView, type BalanceLinks } from './BalancesSection';
import type { ReportState } from './parts';
import { ProfitabilityView } from './ProfitabilitySection';
import { RangeControl, type RangeControlProps } from './RangeControl';
import { ReportsSubnav } from './ReportsSubnav';

// /reports/finance (M7-14b), without any query: the reports sub-navigation
// (M8-09b A3), the title, then
// "Rentabilidad" (the controls and the figures for the range and basis) and
// "Saldos" (today's balances), each with its own loading, error and
// out-of-date states.

export interface FinancialReportViewProps {
  controls: RangeControlProps;
  profitability: ReportState<Profitability>;
  balances: ReportState<Balances>;
  links: BalanceLinks;
}

export function FinancialReportView({
  controls,
  profitability,
  balances,
  links,
}: FinancialReportViewProps) {
  const { t } = useTranslation();
  const ids = { profitability: useId(), balances: useId() };
  return (
    <div className="flex min-w-0 flex-col gap-8" data-financial-report>
      <ReportsSubnav />
      <div className="flex min-w-0 flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('reports.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('reports.intro')}</p>
      </div>
      <section
        aria-labelledby={ids.profitability}
        className="flex min-w-0 flex-col gap-4"
      >
        <h2 id={ids.profitability} className="text-xl font-semibold">
          {t('reports.profitability.title')}
        </h2>
        <RangeControl {...controls} />
        <ProfitabilityView state={profitability} />
      </section>
      <section
        aria-labelledby={ids.balances}
        className="flex min-w-0 flex-col gap-4"
      >
        <h2 id={ids.balances} className="text-xl font-semibold">
          {t('reports.balances.title')}
        </h2>
        <BalancesView state={balances} links={links} />
      </section>
    </div>
  );
}
