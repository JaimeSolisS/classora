import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { Pagination } from '@/features/catalog/Pagination';
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import {
  payslipUrl,
  type EarningsPage,
  type EarningsPeriod,
  type PayslipReference,
} from './api';
import { myPayPeriodPath } from './earningsRules';
import { usePayLabels } from './labels';
import { PaidStateBadge } from './PeriodsView';

// The tutor's finalized periods on /my-pay (M7-10b B), without any query:
// a table from 768 px and cards below it, newest first, the API's offset
// pages. Each row says the dates and frequency (a link to the period), the
// total, what's paid, the last payment's date and the payslip with its
// download link. A period with no lines reads "No activity". Rows whose
// refetch failed stay, marked as possibly out of date: nothing here says
// they were reloaded (#458's rule). Every amount is the API's string.

export type EarningsListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      page: EarningsPage;
      /** The last refetch failed: these rows may be out of date. */
      refreshError: unknown;
      onRefresh: () => void;
    };

export interface EarningsListProps {
  state: EarningsListState;
  /** The signed-in tutor: the payslip links are under their path. */
  tutorUserId: string;
  onPage: (page: number) => void;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

/**
 * A payslip's number and its download link (B2, the `ReceiptLink` pattern):
 * a plain same-origin link, `download` names `<number>.pdf`, a 44 px target.
 * No payslip: "—".
 */
export function PayslipLink({
  tutorUserId,
  payslip,
}: {
  tutorUserId: string;
  payslip: PayslipReference | null;
}) {
  const { t } = useTranslation();
  if (payslip === null) {
    return <span data-payslip="none">—</span>;
  }
  return (
    <span
      className="inline-flex flex-wrap items-center gap-x-2"
      data-payslip={payslip.number}
    >
      <span className="tabular-nums">{payslip.number}</span>
      <a
        href={payslipUrl(tutorUserId, payslip.id)}
        download={`${payslip.number}.pdf`}
        className={cn(CATALOG_LINK_CLASS, 'inline-flex min-h-11 items-center')}
        aria-label={t('payroll.earnings.payslip.downloadFor', {
          number: payslip.number,
        })}
      >
        {t('payroll.earnings.payslip.download')}
      </a>
    </span>
  );
}

/** The words of a row: dates, frequency, total and the last payment. */
function useEarningsText() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatMoney } = useFormatters();
  return (period: EarningsPeriod) => ({
    dates: t('payroll.periods.dates', {
      start: formatLocalDate(period.starts_on),
      end: formatLocalDate(period.ends_on),
    }),
    frequency: labels.frequency(period.pay_frequency),
    total: formatMoney(period.total),
    lastPaid: period.last_paid_on ? formatLocalDate(period.last_paid_on) : '—',
    empty: period.line_count === 0,
  });
}

function Total({
  text,
}: {
  text: ReturnType<ReturnType<typeof useEarningsText>>;
}) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <span className="font-medium">{text.total}</span>
      {text.empty ? (
        <span className="text-sm text-muted-foreground" data-no-activity>
          {t('payroll.earnings.noActivity')}
        </span>
      ) : null}
    </span>
  );
}

function Rows({
  items,
  tutorUserId,
}: {
  items: readonly EarningsPeriod[];
  tutorUserId: string;
}) {
  const { t } = useTranslation();
  const text = useEarningsText();
  const E = 'payroll.earnings';
  const rows = items.map((period) => ({ period, row: text(period) }));
  const open = (period: EarningsPeriod, dates: string) => (
    <Link
      to={myPayPeriodPath(period.id)}
      aria-label={t(`${E}.openFor`, { dates })}
      className={LINK_CLASS}
    >
      {dates}
    </Link>
  );
  return (
    <>
      <div className="hidden md:block" data-earnings-table>
        <Table>
          <TableCaption className="sr-only">{t(`${E}.caption`)}</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t(`${E}.columns.period`)}</TableHead>
              <TableHead scope="col" className="text-right">
                {t(`${E}.columns.total`)}
              </TableHead>
              <TableHead scope="col">{t(`${E}.columns.paid`)}</TableHead>
              <TableHead scope="col">{t(`${E}.columns.lastPaid`)}</TableHead>
              <TableHead scope="col">{t(`${E}.columns.payslip`)}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ period, row }) => (
              <TableRow
                key={period.id}
                data-earnings-id={period.id}
                className="align-top"
              >
                <TableCell className="whitespace-normal">
                  <span className="flex flex-col items-start">
                    {open(period, row.dates)}
                    <span className="text-sm text-muted-foreground">
                      {row.frequency}
                    </span>
                  </span>
                </TableCell>
                <TableCell className="text-right" data-total={period.total}>
                  <Total text={row} />
                </TableCell>
                <TableCell>
                  <PaidStateBadge state={period.paid_state} />
                </TableCell>
                <TableCell data-last-paid>{row.lastPaid}</TableCell>
                <TableCell className="whitespace-normal">
                  <PayslipLink
                    tutorUserId={tutorUserId}
                    payslip={period.payslip}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-earnings-cards>
        {rows.map(({ period, row }) => (
          <li
            key={period.id}
            data-earnings-card={period.id}
            className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              {open(period, row.dates)}
              <PaidStateBadge state={period.paid_state} />
            </div>
            <dl className="grid grid-cols-1 gap-1 text-sm">
              <div className="min-w-0">
                <dt className="font-medium">{t(`${E}.columns.frequency`)}</dt>
                <dd>{row.frequency}</dd>
              </div>
              <div className="min-w-0">
                <dt className="font-medium">{t(`${E}.columns.total`)}</dt>
                <dd>
                  {row.total}
                  {row.empty ? (
                    <span className="block text-muted-foreground">
                      {t(`${E}.noActivity`)}
                    </span>
                  ) : null}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="font-medium">{t(`${E}.columns.lastPaid`)}</dt>
                <dd>{row.lastPaid}</dd>
              </div>
              <div className="min-w-0">
                <dt className="font-medium">{t(`${E}.columns.payslip`)}</dt>
                <dd className="break-words">
                  <PayslipLink
                    tutorUserId={tutorUserId}
                    payslip={period.payslip}
                  />
                </dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

export function EarningsList({
  state,
  tutorUserId,
  onPage,
}: EarningsListProps) {
  const { t } = useTranslation();
  const id = useId();
  const E = 'payroll.earnings';

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { items, total, limit, offset } = state.page;
    const stale = state.refreshError !== null;
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        {stale ? (
          <ErrorAlert
            error={state.refreshError}
            message={t(`${E}.outdated`)}
            onRetry={state.onRefresh}
            retryLabel={t(`${E}.reload`)}
          />
        ) : null}
        {total === 0 && items.length === 0 ? (
          <p className="text-sm" data-earnings-empty>
            {t(`${E}.empty`)}
          </p>
        ) : (
          <div
            className="flex min-w-0 flex-col gap-4"
            data-earnings-stale={stale ? 'true' : undefined}
          >
            <p className="text-sm text-muted-foreground" data-payslip-note>
              {t(`${E}.payslip.language`)}
            </p>
            {items.length > 0 ? (
              <Rows items={items} tutorUserId={tutorUserId} />
            ) : null}
            <Pagination
              offset={offset}
              limit={limit}
              shown={items.length}
              total={total}
              onPage={onPage}
              label={t(`${E}.pagination`)}
              buttonClassName="min-h-11 md:min-h-9"
            />
          </div>
        )}
      </div>
    );
  }

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex min-w-0 flex-col gap-4"
      data-earnings-section
    >
      <div className="flex flex-col gap-2">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {t(`${E}.title`)}
        </h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t(`${E}.intro`)}
        </p>
      </div>
      {content}
    </section>
  );
}
