import { X } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Combobox, type OptionsPage } from '@/components/ui/combobox';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Pagination } from '@/features/catalog/Pagination';
import { StateBadge } from '@/features/users/admin/StateBadge';
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import {
  PAYROLL_PERIODS_KEY,
  type PayrollPeriod,
  type PayrollPeriodListParams,
  type PayrollPeriodPage,
  type PaidState,
  type PayrollTutor,
  type PeriodStatus,
} from './api';
import { usePayLabels } from './labels';
import {
  PAID_STATES,
  PERIOD_STATUSES,
  periodPath,
  REVIEWER_STATUSES,
} from './periodRules';
import { INPUT_CLASS, SELECT_CLASS } from './sheet';
import type { PayrollOffState } from './UnresolvedWorkView';

// The period list (#110b B), without any query: a table from 768 px and cards
// below it, the status and tutor filters, the API's offset pages. Every
// amount is the API's string; a total too large to sum reads so, and the row
// still opens (B4). A tutor who has left shows their state (B3). "No periods"
// is only said when it's true: not under a filter, and not while payroll is
// off. A delegated Administrator sees prepared and finalized periods only, so
// their empty list says nothing is waiting (B5).

export type PeriodsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      page: PayrollPeriodPage;
      /** A refresh failed: these rows may be out of date. */
      refreshError: unknown;
      onRefresh: () => void;
    };

export interface PeriodsViewProps {
  state: PeriodsState;
  /** Whether payroll is on; null when the user can't read the settings. */
  payroll: PayrollOffState | null;
  filters: PayrollPeriodListParams;
  onFilters: (next: Partial<PayrollPeriodListParams>) => void;
  onPage: (page: number) => void;
  /** The tutor picker's search (the Payroll list); null hides the picker. */
  searchTutors:
    | ((
        q: string | null,
        signal: AbortSignal,
      ) => Promise<OptionsPage<PayrollTutor>>)
    | null;
  /** The chosen tutor's name, for the chip. */
  tutorName: string | null;
  onTutorName: (id: string, name: string) => void;
  /** A reviewer who reads prepared and finalized periods only (#110 E8). */
  reviewerOnly: boolean;
  /** Whether the settings page opens for the user. */
  canOpenSettings: boolean;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

const STATUS_STYLE: Record<PeriodStatus, string> = {
  open: 'border-dashed border-border bg-background',
  prepared: 'border-border bg-background',
  finalized: 'border-border bg-muted',
};

/** A period's status as a text badge: the word carries the meaning. */
export function PeriodStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const known = PERIOD_STATUSES.find((item) => item === status);
  return (
    <span
      data-period-status={status}
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
        known ? STATUS_STYLE[known] : 'border-border bg-background',
      )}
    >
      {known ? t(`payroll.periods.statuses.${known}`) : status}
    </span>
  );
}

const PAID_STYLE: Record<PaidState, string> = {
  unpaid: 'border-dashed border-border bg-background',
  partially_paid: 'border-border bg-background',
  paid: 'border-border bg-muted',
};

/** What's paid of a finalized period, as a text badge (M7-09b D). */
export function PaidStateBadge({ state }: { state: string }) {
  const { t } = useTranslation();
  const known = PAID_STATES.find((item) => item === state);
  return (
    <span
      data-paid-state={state}
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
        known ? PAID_STYLE[known] : 'border-border bg-background',
      )}
    >
      {known ? t(`payroll.periods.paidStates.${known}`) : state}
    </span>
  );
}

/** A row's status, and what's paid once it's finalized (D2). */
function RowStatus({ period }: { period: PayrollPeriod }) {
  return (
    <span className="flex flex-wrap items-center gap-2">
      <PeriodStatusBadge status={period.status} />
      {period.status === 'finalized' && period.paid_state ? (
        <PaidStateBadge state={period.paid_state} />
      ) : null}
    </span>
  );
}

/** The words of one row: dates, frequency and the total as the API sent it. */
function usePeriodText() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate, formatMoney } = useFormatters();
  return (
    period: Pick<PayrollPeriod, 'starts_on' | 'ends_on' | 'pay_frequency'> & {
      total: string | null;
    },
  ) => ({
    dates: t('payroll.periods.dates', {
      start: formatLocalDate(period.starts_on),
      end: formatLocalDate(period.ends_on),
    }),
    frequency: labels.frequency(period.pay_frequency),
    total:
      period.total === null
        ? t('payroll.periods.tooLarge')
        : formatMoney(period.total),
  });
}

function Tutor({ period }: { period: PayrollPeriod }) {
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="break-words">{period.tutor_display_name}</span>
      {period.tutor_state !== 'active' ? (
        <StateBadge state={period.tutor_state} />
      ) : null}
    </span>
  );
}

function Rows({ items }: { items: readonly PayrollPeriod[] }) {
  const { t } = useTranslation();
  const text = usePeriodText();
  return (
    <>
      <div className="hidden md:block" data-periods-table>
        <Table>
          <TableCaption className="sr-only">
            {t('payroll.periods.caption')}
          </TableCaption>
          <TableHeader>
            <TableRow>
              {(['tutor', 'dates', 'frequency', 'status'] as const).map(
                (column) => (
                  <TableHead key={column} scope="col">
                    {t(`payroll.periods.columns.${column}`)}
                  </TableHead>
                ),
              )}
              <TableHead scope="col" className="text-right">
                {t('payroll.periods.columns.lines')}
              </TableHead>
              <TableHead scope="col" className="text-right">
                {t('payroll.periods.columns.total')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((period) => {
              const row = text(period);
              return (
                <TableRow
                  key={period.id}
                  data-period-id={period.id}
                  className="align-top"
                >
                  <TableCell className="whitespace-normal">
                    <Tutor period={period} />
                  </TableCell>
                  <TableCell>
                    <Link
                      to={periodPath(period.id)}
                      aria-label={t('payroll.periods.openFor', {
                        name: period.tutor_display_name,
                        dates: row.dates,
                      })}
                      className={LINK_CLASS}
                    >
                      {row.dates}
                    </Link>
                  </TableCell>
                  <TableCell>{row.frequency}</TableCell>
                  <TableCell>
                    <RowStatus period={period} />
                  </TableCell>
                  <TableCell className="text-right">
                    {period.line_count}
                  </TableCell>
                  <TableCell
                    className="text-right font-medium"
                    data-total={period.total ?? 'null'}
                  >
                    {row.total}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-periods-cards>
        {items.map((period) => {
          const row = text(period);
          return (
            <li
              key={period.id}
              data-period-card={period.id}
              className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Tutor period={period} />
                <RowStatus period={period} />
              </div>
              <Link
                to={periodPath(period.id)}
                aria-label={t('payroll.periods.openFor', {
                  name: period.tutor_display_name,
                  dates: row.dates,
                })}
                className={LINK_CLASS}
              >
                {row.dates}
              </Link>
              <dl className="grid grid-cols-1 gap-1 text-sm">
                {(
                  [
                    [t('payroll.periods.columns.frequency'), row.frequency],
                    [
                      t('payroll.periods.columns.lines'),
                      String(period.line_count),
                    ],
                    [t('payroll.periods.columns.total'), row.total],
                  ] as const
                ).map(([term, value]) => (
                  <div key={term} className="min-w-0">
                    <dt className="font-medium">{term}</dt>
                    <dd className="break-words">{value}</dd>
                  </div>
                ))}
              </dl>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function PeriodsView({
  state,
  payroll,
  filters,
  onFilters,
  onPage,
  searchTutors,
  tutorName,
  onTutorName,
  reviewerOnly,
  canOpenSettings,
}: PeriodsViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    filters: `${id}-filters`,
    status: `${id}-status`,
    paid: `${id}-paid`,
  };
  const filtered =
    filters.status !== null || filters.tutor !== null || filters.paid !== null;
  const off = payroll?.status === 'ready' && payroll.off;
  const statuses = reviewerOnly ? REVIEWER_STATUSES : PERIOD_STATUSES;
  const chipName = tutorName ?? t('payroll.periods.filters.selected');

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && !state.refreshError) {
    if (filtered) {
      content = (
        <p role="status" className="text-sm" data-periods-empty="filtered">
          {t('payroll.periods.emptyFiltered')}
        </p>
      );
    } else if (payroll?.status === 'loading') {
      content = <LoadingState rows={1} />;
    } else if (payroll?.status === 'error') {
      content = <ErrorAlert error={payroll.error} onRetry={payroll.onRetry} />;
    } else if (off) {
      content = null;
    } else {
      content = (
        <p role="status" className="text-sm" data-periods-empty>
          {reviewerOnly
            ? t('payroll.periods.emptyReviewer')
            : t('payroll.periods.empty')}
        </p>
      );
    }
  } else {
    const { items, total, limit, offset } = state.page;
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        {state.refreshError ? (
          <ErrorAlert
            error={state.refreshError}
            message={t('payroll.period.outdatedNotice')}
            onRetry={state.onRefresh}
            retryLabel={t('payroll.period.reload')}
          />
        ) : null}
        {items.length > 0 ? <Rows items={items} /> : null}
        <Pagination
          offset={offset}
          limit={limit}
          shown={items.length}
          total={total}
          onPage={onPage}
          buttonClassName="min-h-11 md:min-h-9"
        />
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('payroll.periods.title')}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('payroll.periods.intro')}
        </p>
      </div>
      {off ? (
        <p
          className="flex flex-wrap items-center gap-x-2 text-sm"
          data-payroll-off
        >
          <span>{t('payroll.periods.payrollOff')}</span>
          {canOpenSettings ? (
            <Link to="/payroll/settings" className={LINK_CLASS}>
              {t('payroll.periods.payrollOffLink')}
            </Link>
          ) : null}
        </p>
      ) : null}
      <section
        aria-labelledby={ids.filters}
        className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
      >
        <h2 id={ids.filters} className="text-lg font-semibold">
          {t('payroll.periods.filters.label')}
        </h2>
        <div className="grid min-w-0 gap-4 md:grid-cols-3">
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={ids.status} className="text-sm font-medium">
              {t('payroll.periods.filters.status')}
            </label>
            <select
              id={ids.status}
              className={SELECT_CLASS}
              value={filters.status ?? ''}
              onChange={(event) =>
                onFilters({
                  status:
                    PERIOD_STATUSES.find(
                      (status) => status === event.target.value,
                    ) ?? null,
                })
              }
            >
              <option value="">
                {t('payroll.periods.filters.allStatuses')}
              </option>
              {statuses.map((status) => (
                <option key={status} value={status}>
                  {t(`payroll.periods.statuses.${status}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={ids.paid} className="text-sm font-medium">
              {t('payroll.periods.filters.paid')}
            </label>
            <select
              id={ids.paid}
              className={SELECT_CLASS}
              value={filters.paid ?? ''}
              aria-describedby={`${ids.paid}-help`}
              onChange={(event) =>
                onFilters({
                  paid:
                    PAID_STATES.find((paid) => paid === event.target.value) ??
                    null,
                })
              }
            >
              <option value="">{t('payroll.periods.filters.allPaid')}</option>
              {PAID_STATES.map((paid) => (
                <option key={paid} value={paid}>
                  {t(`payroll.periods.paidStates.${paid}`)}
                </option>
              ))}
            </select>
            <p
              id={`${ids.paid}-help`}
              className="text-sm text-muted-foreground"
            >
              {t('payroll.periods.filters.paidHelp')}
            </p>
          </div>
          {searchTutors ? (
            <div className="min-w-0">
              <Combobox<PayrollTutor>
                label={t('payroll.periods.filters.tutor')}
                help={t('payroll.periods.filters.tutorHelp')}
                queryKey={[...PAYROLL_PERIODS_KEY, 'tutors']}
                load={searchTutors}
                optionId={(tutor) => tutor.user_id}
                optionLabel={(tutor) => tutor.display_name}
                emptyText={t('combobox.emptyTutors')}
                selected={
                  filters.tutor ? { id: filters.tutor, label: chipName } : null
                }
                onSelect={(tutor) => {
                  onTutorName(tutor.user_id, tutor.display_name);
                  onFilters({ tutor: tutor.user_id });
                }}
                inputClassName={INPUT_CLASS}
                optionClassName="min-h-11 md:min-h-9"
              />
            </div>
          ) : null}
        </div>
        {filtered ? (
          <div className="flex flex-wrap items-center gap-2">
            {filters.tutor ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-auto min-h-11 whitespace-normal md:min-h-8"
                aria-label={t('payroll.periods.filters.remove', {
                  name: chipName,
                })}
                onClick={() => onFilters({ tutor: null })}
              >
                {t('payroll.periods.filters.chip', { name: chipName })}
                <X aria-hidden="true" />
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="min-h-11 md:min-h-8"
              onClick={() =>
                onFilters({ status: null, tutor: null, paid: null })
              }
            >
              {t('payroll.periods.filters.clear')}
            </Button>
          </div>
        ) : null}
      </section>
      {content}
    </div>
  );
}
