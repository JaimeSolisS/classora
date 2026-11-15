import { ArrowLeft } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import { useFormatters } from '@/lib/formatters';
import { formatRate } from '@/lib/money';
import { cn } from '@/lib/utils';
import type { EarningsPayment, EarningsPeriodDetail, PayslipLine } from './api';
import { PayslipLink } from './EarningsList';
import {
  EARNINGS_GROUPS,
  groupEarningsLines,
  hoursAndMinutes,
  type EarningsGroup,
} from './earningsRules';
import { EarningsTable, type EarningsColumn } from './EarningsTable';
import { usePayLabels } from './labels';
import { remainingOf } from './paymentForm';
import { percentText } from './payRuleForm';
import { adjustmentKindKey, lineKindKey } from './periodRules';
import { PaidStateBadge } from './PeriodsView';

// A finalized period of the signed-in tutor (M7-10b C), without any query:
// the head (dates, frequency, finalized date, total, what's paid and what's
// left, the payslip), the summary, the lines in the payslip's groups (an
// empty group is hidden) and the payments, a reversed one struck through
// with its date and no reason. No student names: the API sends none. Every
// figure is the API's string, formatted; nothing is added up here. A refetch
// that failed leaves the period shown, marked as possibly out of date; it
// never says it reloaded. A 404 is "not found", whatever the cause (A2).

export type EarningsPeriodState =
  | { status: 'loading' }
  | { status: 'notFound' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      detail: EarningsPeriodDetail;
      /** The last refetch failed: the period may be out of date. */
      refreshError: unknown;
      onRefresh: () => void;
    };

export interface EarningsPeriodViewProps {
  state: EarningsPeriodState;
  tutorUserId: string;
}

const E = 'payroll.earningsPeriod';

const LINK_CLASS =
  'inline-flex min-h-11 items-center gap-1 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

function BackLink() {
  const { t } = useTranslation();
  return (
    <Link to="/my-pay" className={LINK_CLASS} data-back-to-my-pay>
      <ArrowLeft aria-hidden="true" className="size-4" />
      {t(`${E}.back`)}
    </Link>
  );
}

function useLineCells() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatMoney } = useFormatters();
  const L = 'payroll.period.lines';
  const date = (value: string | null) => (value ? formatLocalDate(value) : '—');
  const range = (dates: { starts_on: string; ends_on: string }) =>
    t('payroll.periods.dates', {
      start: formatLocalDate(dates.starts_on),
      end: formatLocalDate(dates.ends_on),
    });
  const amount = (line: PayslipLine) => (
    <span data-amount={line.amount}>{formatMoney(line.amount)}</span>
  );

  const rule = (line: PayslipLine): string | null => {
    const value = line.pay_rule;
    if (!value) return null;
    if (value.mode === 'percent' && value.percent !== null) {
      return t(`${E}.rules.percent`, { percent: percentText(value.percent) });
    }
    if (value.mode === 'fixed' && value.amount !== null) {
      return t(`${E}.rules.fixed`, { amount: formatMoney(value.amount) });
    }
    return t(`${E}.rules.none`);
  };

  const classes = (line: PayslipLine): Record<string, ReactNode> => {
    const kind = lineKindKey(line.kind);
    const event = t(`${L}.kinds.${kind}`);
    const ruleText = rule(line);
    let rate: ReactNode = '—';
    if (line.covered_by_salary) {
      rate = <span data-covered>{t(`${E}.coveredBySalary`)}</span>;
    } else if (line.rate !== null && line.rate_model !== null) {
      const base = t(`${L}.rate`, {
        model: labels.model(line.rate_model),
        rate: formatRate(line.rate),
      });
      rate =
        line.cumulative_sessions !== null && line.cohort_sessions !== null
          ? t(`${E}.courseShare`, {
              rate: base,
              k: line.cumulative_sessions,
              n: line.cohort_sessions,
            })
          : base;
    }
    return {
      date: date(line.service_date),
      classKind: line.class_kind ? labels.kind(line.class_kind) : '—',
      event: ruleText
        ? t(`${E}.eventWithRule`, { event, rule: ruleText })
        : event,
      minutes:
        line.minutes === null
          ? '—'
          : t(`${L}.minutes`, { count: line.minutes }),
      rate,
      amount: amount(line),
    };
  };

  const salary = (line: PayslipLine): Record<string, ReactNode> => {
    const piece = line.salary;
    return {
      piece: piece ? range(piece) : '—',
      detail: piece
        ? t(`${E}.salaryDetail`, {
            numerator: piece.numerator,
            denominator: piece.denominator,
            amount: formatMoney(piece.amount),
            basis: t(`${L}.bases.${piece.basis}`),
          })
        : '—',
      amount: amount(line),
    };
  };

  const item = (line: PayslipLine): Record<string, ReactNode> => ({
    type: line.pay_item?.type_name ?? '—',
    description: (
      <span className="whitespace-pre-line">
        {line.pay_item?.description ?? '—'}
      </span>
    ),
    amount: amount(line),
  });

  const adjustment = (line: PayslipLine): Record<string, ReactNode> => {
    const value = line.adjustment;
    const kind = adjustmentKindKey(value?.kind ?? null);
    const details: string[] = [];
    if (value?.service_date) {
      details.push(
        value.class_kind
          ? t(`${E}.adjustments.workKind`, {
              date: formatLocalDate(value.service_date),
              kind: labels.kind(value.class_kind),
            })
          : t(`${E}.adjustments.work`, {
              date: formatLocalDate(value.service_date),
            }),
      );
    }
    if (value?.origin_period) {
      details.push(
        t(`${E}.adjustments.origin`, { period: range(value.origin_period) }),
      );
    }
    if (value?.paired_period) {
      details.push(
        t(`${E}.adjustments.paired`, { period: range(value.paired_period) }),
      );
    }
    return {
      kind: kind
        ? t(`${L}.adjustmentKinds.${kind}`)
        : t(`${L}.kinds.adjustment`),
      details: (
        <span className="flex min-w-0 flex-col gap-1">
          {details.length === 0 && !value?.reason ? <span>—</span> : null}
          {details.map((detail, index) => (
            <span key={index}>{detail}</span>
          ))}
          {value?.reason ? (
            <span className="whitespace-pre-line" data-adjustment-reason>
              {t(`${L}.reason`, { reason: value.reason })}
            </span>
          ) : null}
        </span>
      ),
      amount: amount(line),
    };
  };

  return { classes, salary, item, adjustment };
}

const GROUP_COLUMNS: Record<EarningsGroup, readonly ColumnKey[]> = {
  classes: ['date', 'classKind', 'event', 'minutes', 'rate', 'amount'],
  salary: ['piece', 'detail', 'amount'],
  bonuses: ['type', 'description', 'amount'],
  deductions: ['type', 'description', 'amount'],
  adjustments: ['kind', 'details', 'amount'],
};

type ColumnKey =
  | 'date'
  | 'classKind'
  | 'event'
  | 'minutes'
  | 'rate'
  | 'amount'
  | 'piece'
  | 'detail'
  | 'type'
  | 'description'
  | 'kind'
  | 'details';

function Lines({ lines }: { lines: readonly PayslipLine[] }) {
  const { t } = useTranslation();
  const cells = useLineCells();
  const groups = groupEarningsLines(lines);
  const id = useId();
  const visible = EARNINGS_GROUPS.filter((group) => groups[group].length > 0);
  if (visible.length === 0) {
    return (
      <p className="text-sm" data-earnings-no-lines>
        {t(`${E}.noLines`)}
      </p>
    );
  }
  return (
    <>
      {visible.map((group) => {
        const columns: EarningsColumn[] = GROUP_COLUMNS[group].map((key) => ({
          key,
          label: t(`${E}.columns.${key}`),
          numeric: key === 'amount',
        }));
        const row =
          group === 'classes'
            ? cells.classes
            : group === 'salary'
              ? cells.salary
              : group === 'adjustments'
                ? cells.adjustment
                : cells.item;
        return (
          <section
            key={group}
            aria-labelledby={`${id}-${group}`}
            className="flex min-w-0 flex-col gap-3"
            data-earnings-group={group}
          >
            <h3 id={`${id}-${group}`} className="text-base font-semibold">
              {t(`${E}.groups.${group}`)}
            </h3>
            <EarningsTable
              caption={t(`${E}.groups.${group}`)}
              columns={columns}
              rows={groups[group].map((line, index) => ({
                key: String(index),
                cells: row(line),
                data: {
                  'data-line-kind': line.kind,
                  ...(line.adjustment
                    ? { 'data-adjustment-kind': line.adjustment.kind }
                    : {}),
                },
              }))}
            />
          </section>
        );
      })}
    </>
  );
}

function Payments({ payments }: { payments: readonly EarningsPayment[] }) {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const methods = useAccountLabels();
  if (payments.length === 0) {
    return (
      <p className="text-sm" data-earnings-no-payments>
        {t(`${E}.payments.empty`)}
      </p>
    );
  }
  const columns: EarningsColumn[] = (
    ['date', 'amount', 'method', 'reference', 'status'] as const
  ).map((key) => ({
    key,
    label: t(`${E}.payments.columns.${key}`),
    numeric: key === 'amount',
  }));
  return (
    <EarningsTable
      caption={t(`${E}.payments.caption`)}
      columns={columns}
      rows={payments.map((payment) => ({
        key: payment.id,
        data: {
          'data-payment-id': payment.id,
          ...(payment.reversed_on ? { 'data-reversed': 'true' } : {}),
        },
        cells: {
          date: formatLocalDate(payment.paid_on),
          amount: (
            <span
              className={cn(payment.reversed_on && 'line-through')}
              data-payment-amount={payment.amount}
            >
              {formatMoney(payment.amount)}
            </span>
          ),
          method: methods.method(payment.method_code),
          reference: payment.reference || '—',
          status: payment.reversed_on ? (
            <span className="font-medium" data-payment-reversed>
              {t(`${E}.payments.reversedOn`, {
                date: formatLocalDate(payment.reversed_on),
              })}
            </span>
          ) : (
            '—'
          ),
        },
      }))}
    />
  );
}

function Figures({
  items,
  attribute,
}: {
  items: readonly (readonly [string, string, ReactNode])[];
  attribute: string;
}) {
  return (
    <dl className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {items.map(([key, term, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-sm font-medium text-muted-foreground">{term}</dt>
          <dd className="font-semibold break-words" {...{ [attribute]: key }}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Ready({
  detail,
  tutorUserId,
  stale,
}: {
  detail: EarningsPeriodDetail;
  tutorUserId: string;
  stale: boolean;
}) {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatMoney } = useFormatters();
  const id = useId();
  const { period, summary } = detail;
  const time = hoursAndMinutes(summary.class_minutes);
  const remaining =
    period.paid_state === 'partially_paid'
      ? remainingOf({
          total_amount: period.total,
          paid_total: period.paid_total,
        })
      : null;

  const head: [string, string, ReactNode][] = [
    [
      'frequency',
      t(`${E}.head.frequency`),
      labels.frequency(period.pay_frequency),
    ],
    [
      'finalizedOn',
      t(`${E}.head.finalizedOn`),
      formatLocalDate(period.finalized_on),
    ],
    ['total', t(`${E}.head.total`), formatMoney(detail.total)],
    ['paid', t(`${E}.head.paid`), formatMoney(period.paid_total)],
  ];
  if (remaining !== null) {
    head.push(['remaining', t(`${E}.head.remaining`), formatMoney(remaining)]);
  }
  head.push([
    'state',
    t(`${E}.head.state`),
    <PaidStateBadge key="state" state={period.paid_state} />,
  ]);

  const totals: [string, string, ReactNode][] = [
    [
      'classLines',
      t(`${E}.summary.classLines`),
      String(summary.class_line_count),
    ],
    [
      'classTime',
      t(`${E}.summary.classTime`),
      t(`${E}.summary.classTimeValue`, time),
    ],
    ['classes', t(`${E}.summary.classes`), formatMoney(summary.classes)],
    ['salary', t(`${E}.summary.salary`), formatMoney(summary.salary)],
    ['bonuses', t(`${E}.summary.bonuses`), formatMoney(summary.bonuses)],
    [
      'deductions',
      t(`${E}.summary.deductions`),
      formatMoney(summary.deductions),
    ],
    [
      'adjustments',
      t(`${E}.summary.adjustments`),
      formatMoney(summary.adjustments),
    ],
    ['total', t(`${E}.summary.total`), formatMoney(detail.total)],
  ];

  return (
    <div
      className="flex min-w-0 flex-col gap-8"
      data-earnings-period-stale={stale ? 'true' : undefined}
    >
      <section className="flex min-w-0 flex-col gap-4" data-earnings-head>
        <Figures items={head} attribute="data-head" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-sm font-medium text-muted-foreground">
            {t(`${E}.head.payslip`)}
          </p>
          <PayslipLink tutorUserId={tutorUserId} payslip={detail.payslip} />
          {detail.payslip ? (
            <p className="text-sm text-muted-foreground" data-payslip-note>
              {t('payroll.earnings.payslip.language')}
            </p>
          ) : null}
        </div>
      </section>
      <section
        aria-labelledby={`${id}-summary`}
        className="flex min-w-0 flex-col gap-4"
        data-earnings-summary
      >
        <h2 id={`${id}-summary`} className="text-lg font-semibold">
          {t(`${E}.summary.title`)}
        </h2>
        <Figures items={totals} attribute="data-summary" />
      </section>
      <section
        aria-labelledby={`${id}-lines`}
        className="flex min-w-0 flex-col gap-6"
        data-earnings-lines
      >
        <h2 id={`${id}-lines`} className="text-lg font-semibold">
          {t(`${E}.linesTitle`)}
        </h2>
        <Lines lines={detail.lines} />
      </section>
      <section
        aria-labelledby={`${id}-payments`}
        className="flex min-w-0 flex-col gap-4"
        data-earnings-payments
      >
        <h2 id={`${id}-payments`} className="text-lg font-semibold">
          {t(`${E}.payments.title`)}
        </h2>
        <Payments payments={detail.payments} />
      </section>
    </div>
  );
}

export function EarningsPeriodView({
  state,
  tutorUserId,
}: EarningsPeriodViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();

  if (state.status === 'notFound') {
    return (
      <div className="flex min-w-0 flex-col items-start gap-4">
        <BackLink />
        <h1 className="text-2xl font-semibold">{t(`${E}.notFoundTitle`)}</h1>
        <p data-earnings-not-found>{t(`${E}.notFound`)}</p>
      </div>
    );
  }

  const title =
    state.status === 'ready'
      ? t(`${E}.title`, {
          start: formatLocalDate(state.detail.period.starts_on),
          end: formatLocalDate(state.detail.period.ends_on),
        })
      : t(`${E}.fallbackTitle`);

  let content;
  if (state.status === 'loading') {
    content = <LoadingState rows={6} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        {state.refreshError ? (
          <ErrorAlert
            error={state.refreshError}
            message={t(`${E}.outdated`)}
            onRetry={state.onRefresh}
            retryLabel={t('payroll.earnings.reload')}
          />
        ) : null}
        <Ready
          detail={state.detail}
          tutorUserId={tutorUserId}
          stale={state.refreshError !== null}
        />
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col items-start gap-2">
        <BackLink />
        <h1 className="text-2xl font-semibold break-words">{title}</h1>
      </div>
      {content}
    </div>
  );
}
