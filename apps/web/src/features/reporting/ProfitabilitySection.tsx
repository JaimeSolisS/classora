import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { StateBadge } from '@/features/finance/Badges';
import { useCategoryLabel } from '@/features/finance/labels';
import { useFormatters } from '@/lib/formatters';
import type { ClassRow, Profitability } from './api';
import { FigureRow, Money, ReportStatus, type ReportState } from './parts';

// The profitability figures (M7-14b B2–B6), without any query. Accrual: the
// class rows in the API's order with a bold total, a table from `sm` up and
// cards below. Cash: the centre-wide summary only. Both: the operating
// expenses by current category label and by vendor, then the net result,
// every amount as the API sent it (never recomputed here), and the E3 notes
// under the figures they explain.

const L = 'reports.profitability';
const NOTE = 'text-sm text-muted-foreground';

function ClassRowsTable({ report }: { report: Profitability }) {
  const { t } = useTranslation();
  const captionId = useId();
  const name = (row: ClassRow) => t(`${L}.classTypes.${row.class_type}`);
  return (
    <>
      <div className="hidden sm:block" data-report-class-table>
        <Table aria-labelledby={captionId}>
          <TableCaption id={captionId} className="sr-only">
            {t(`${L}.accrualCaption`)}
          </TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t(`${L}.columns.classType`)}</TableHead>
              <TableHead scope="col" className="text-right">
                {t(`${L}.columns.revenue`)}
              </TableHead>
              <TableHead scope="col" className="text-right">
                {t(`${L}.columns.cost`)}
              </TableHead>
              <TableHead scope="col" className="text-right">
                {t(`${L}.columns.grossProfit`)}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {report.class_rows.map((row) => (
              <TableRow key={row.class_type} data-class-type={row.class_type}>
                <TableHead scope="row" className="font-normal">
                  {name(row)}
                </TableHead>
                <TableCell className="text-right">
                  <Money amount={row.revenue} />
                </TableCell>
                <TableCell className="text-right">
                  <Money amount={row.direct_tutor_cost} />
                </TableCell>
                <TableCell className="text-right">
                  <Money amount={row.gross_profit} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
          <TableFooter>
            <TableRow className="font-semibold" data-class-type="total">
              <TableHead scope="row">{t(`${L}.total`)}</TableHead>
              <TableCell className="text-right">
                <Money amount={report.revenue_total} />
              </TableCell>
              <TableCell className="text-right">
                <Money amount={report.direct_tutor_cost_total} />
              </TableCell>
              <TableCell className="text-right">
                <Money amount={report.gross_profit} />
              </TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 sm:hidden" data-report-class-cards>
        {[
          ...report.class_rows.map((row) => ({
            key: row.class_type,
            title: name(row),
            revenue: row.revenue,
            cost: row.direct_tutor_cost,
            profit: row.gross_profit,
            strong: false,
          })),
          {
            key: 'total',
            title: t(`${L}.total`),
            revenue: report.revenue_total,
            cost: report.direct_tutor_cost_total,
            profit: report.gross_profit,
            strong: true,
          },
        ].map((card) => (
          <li
            key={card.key}
            data-class-card={card.key}
            className={`flex min-w-0 flex-col gap-1 rounded-lg border p-4 ${
              card.strong ? 'border-foreground' : 'border-border'
            }`}
          >
            <p className={card.strong ? 'font-semibold' : 'font-medium'}>
              {card.title}
            </p>
            <dl className="flex flex-col text-sm">
              <FigureRow
                label={t(`${L}.columns.revenue`)}
                amount={card.revenue}
              />
              <FigureRow label={t(`${L}.columns.cost`)} amount={card.cost} />
              <FigureRow
                label={t(`${L}.columns.grossProfit`)}
                amount={card.profit}
                strong
              />
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

function CashSummary({ report }: { report: Profitability }) {
  const { t } = useTranslation();
  return (
    <dl
      className="flex max-w-xl min-w-0 flex-col rounded-lg border border-border p-4"
      data-report-cash
    >
      <FigureRow
        label={t(`${L}.cash.revenue`)}
        amount={report.revenue_total}
        data="revenue"
      />
      <FigureRow
        label={t(`${L}.cash.payroll`)}
        amount={report.direct_tutor_cost_total}
        data="payroll"
      />
      <FigureRow
        label={t(`${L}.cash.expenses`)}
        amount={report.operating_expenses.total}
        data="expenses"
      />
      <FigureRow
        label={t(`${L}.cash.net`)}
        amount={report.net_result}
        strong
        data="net"
      />
    </dl>
  );
}

function OperatingExpenses({ report }: { report: Profitability }) {
  const { t } = useTranslation();
  const categoryLabel = useCategoryLabel();
  const titleId = useId();
  const {
    total,
    by_category: categories,
    by_vendor: vendors,
  } = report.operating_expenses;
  const empty = categories.length === 0 && vendors.length === 0;
  const item =
    'flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-1';
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-3"
      data-report-expenses
    >
      <h3 id={titleId} className="text-lg font-semibold">
        {t(`${L}.expenses.title`)}
      </h3>
      <dl className="flex max-w-xl min-w-0 flex-col">
        <FigureRow
          label={t(`${L}.expenses.total`)}
          amount={total}
          data="expenses-total"
        />
      </dl>
      {empty ? (
        <p data-report-expenses-empty>{t(`${L}.expenses.empty`)}</p>
      ) : (
        <div className="grid min-w-0 grid-cols-1 gap-6 md:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-2">
            <h4 className="font-medium">{t(`${L}.expenses.byCategory`)}</h4>
            <ul className="flex flex-col" data-report-by-category>
              {categories.map(({ category, amount }) => (
                <li
                  key={category.id}
                  className={item}
                  data-category-id={category.id}
                >
                  <span className="flex min-w-0 flex-wrap items-center gap-1">
                    <span className="[overflow-wrap:anywhere]">
                      {categoryLabel(category)}
                    </span>
                    {category.is_active ? null : (
                      <StateBadge active={false}>
                        {t(`${L}.expenses.inactiveCategory`)}
                      </StateBadge>
                    )}
                  </span>
                  <Money amount={amount} />
                </li>
              ))}
            </ul>
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <h4 className="font-medium">{t(`${L}.expenses.byVendor`)}</h4>
            <ul className="flex flex-col" data-report-by-vendor>
              {vendors.map(({ vendor, amount }) => (
                <li
                  key={vendor?.id ?? 'none'}
                  className={item}
                  data-vendor-id={vendor?.id ?? 'none'}
                >
                  <span className="flex min-w-0 flex-wrap items-center gap-1">
                    <span
                      className={
                        vendor
                          ? '[overflow-wrap:anywhere]'
                          : 'text-muted-foreground italic'
                      }
                    >
                      {vendor ? vendor.name : t(`${L}.expenses.noVendor`)}
                    </span>
                    {vendor && !vendor.is_active ? (
                      <StateBadge active={false}>
                        {t(`${L}.expenses.inactiveVendor`)}
                      </StateBadge>
                    ) : null}
                  </span>
                  <Money amount={amount} />
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}

function NetResult({ report }: { report: Profitability }) {
  const { t } = useTranslation();
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-2"
      data-report-net
    >
      <h3 id={titleId} className="text-lg font-semibold">
        {t(`${L}.net.title`)}
      </h3>
      <dl className="flex max-w-xl min-w-0 flex-col rounded-lg border border-border p-4">
        <FigureRow
          label={t(`${L}.net.grossProfit`)}
          amount={report.gross_profit}
        />
        <FigureRow
          label={t(`${L}.net.expenses`)}
          amount={report.operating_expenses.total}
        />
        <FigureRow
          label={t(`${L}.net.result`)}
          amount={report.net_result}
          strong
          data="net"
        />
      </dl>
    </section>
  );
}

export function ProfitabilityFigures({ report }: { report: Profitability }) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const accrual = report.basis === 'accrual';
  return (
    <div
      className="flex min-w-0 flex-col gap-6"
      data-report-basis-shown={report.basis}
    >
      <p className="text-sm font-medium" data-report-range>
        {t(`${L}.range.caption`, {
          from: formatLocalDate(report.from),
          to: formatLocalDate(report.to),
        })}
      </p>
      {accrual ? (
        <div className="flex min-w-0 flex-col gap-2">
          <ClassRowsTable report={report} />
          <p className={NOTE} data-report-note="accrual">
            {t(`${L}.notes.accrual`)}
          </p>
          <p className={NOTE} data-report-note="unallocated">
            {t(`${L}.notes.unallocated`)}
          </p>
        </div>
      ) : (
        <div className="flex min-w-0 flex-col gap-2">
          <CashSummary report={report} />
          <p className={NOTE} data-report-note="cash">
            {t(`${L}.notes.cash`)}
          </p>
        </div>
      )}
      <OperatingExpenses report={report} />
      {accrual ? <NetResult report={report} /> : null}
    </div>
  );
}

export function ProfitabilityView({
  state,
}: {
  state: ReportState<Profitability>;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-4" data-report-profitability>
      <ReportStatus state={state} label={t('feedback.loading')} rows={5}>
        {(report) => <ProfitabilityFigures report={report} />}
      </ReportStatus>
    </div>
  );
}
