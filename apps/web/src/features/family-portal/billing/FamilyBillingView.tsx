import { AlertTriangle } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { ReversedBadge } from '@/features/billing/accounts/AccountBadges';
import { apiCents } from '@/features/billing/accounts/cents';
import { ReceiptLink } from '@/features/billing/accounts/ReceiptLink';
import { DueDateCell } from '@/features/billing/due-dates/DueDateCell';
import { ChargeStatusBadge } from '@/features/billing/tuition/TuitionBadges';
import { Pagination } from '@/features/catalog/Pagination';
import { SELECT_CLASS } from '@/features/students/styles';
import { cn } from '@/lib/utils';
import type {
  AccountSummary,
  FamilyChargePage,
  FamilyPackagePage,
  FamilyPaymentPage,
} from './api';
import type { LedgerPage } from './api';
import { FamilyPackageBadges } from './PackageBadges';
import { usePortalLabels } from './labels';
import { CHARGE_FILTERS, type ChargeFilter } from './rules';

// The family billing portal without any query (#388 B-G; stories render
// each state): the h1, the child filter (2+ children; one child is named
// under the h1), the balance cards (the selected child's outstanding and
// overdue; account credit and net balance stay family-wide), Charges (the
// status filter, pages; a card opens its detail), Payments (family-wide,
// with the receipt), Packages and History ("Load more"). Mobile first: every
// list is a list of cards at every width (no table, so nothing scrolls
// sideways at 320 px), and every control is at least 44 px high. Money is
// the API's strings formatted, never recomputed (C1). Staff-only fields
// (note, reasons, who did what) aren't in these types at all (E4).

export type PageState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: T };

export type LedgerState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: LedgerPage['items'];
      hasMore: boolean;
      loadingMore: boolean;
      onLoadMore: () => void;
    };

export interface FamilyBillingViewProps {
  summary: AccountSummary;
  /** The selected child (already checked against the summary), or all. */
  studentId: string | null;
  onStudent: (studentId: string | null) => void;
  filter: ChargeFilter;
  onFilter: (filter: ChargeFilter) => void;
  charges: PageState<FamilyChargePage>;
  onChargesPage: (page: number) => void;
  payments: PageState<FamilyPaymentPage>;
  onPaymentsPage: (page: number) => void;
  packages: PageState<FamilyPackagePage>;
  onPackagesPage: (page: number) => void;
  ledger: LedgerState;
  onOpenCharge: (chargeId: string) => void;
  onOpenPayment: (paymentId: string) => void;
  onOpenPackage: (packageId: string) => void;
}

/** At least 44 × 44 px (I1). */
export const TOUCH = 'min-h-11';
const CARD = 'flex min-w-0 flex-col gap-3 rounded-md border border-border p-4';
const CARD_LIST = 'grid grid-cols-1 gap-3 lg:grid-cols-2';
const TERMS = 'grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1';

function Terms({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className={TERMS}>
      {items.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-sm text-muted-foreground">{term}</dt>
          <dd className="text-sm break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function stateBody<T>(
  state: PageState<T> | LedgerState,
  ready: () => ReactNode,
): ReactNode {
  if (state.status === 'loading') {
    return <LoadingState rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  return ready();
}

export function FamilyBillingView({
  summary,
  studentId,
  onStudent,
  filter,
  onFilter,
  charges,
  onChargesPage,
  payments,
  onPaymentsPage,
  packages,
  onPackagesPage,
  ledger,
  onOpenCharge,
  onOpenPayment,
  onOpenPackage,
}: FamilyBillingViewProps) {
  const { t } = useTranslation();
  const labels = usePortalLabels();
  const id = useId();
  const ids = {
    summary: `${id}-summary`,
    children: `${id}-children`,
    child: `${id}-child`,
    childHelp: `${id}-child-help`,
    filter: `${id}-filter`,
    charges: `${id}-charges`,
    payments: `${id}-payments`,
    packages: `${id}-packages`,
    history: `${id}-history`,
  };
  const studentNames = Object.fromEntries(
    summary.students.map((item) => [item.student.id, item.student.name]),
  );
  const several = summary.students.length > 1;
  const only = summary.students.length === 1 ? summary.students[0] : null;
  const childRow =
    studentId !== null
      ? (summary.students.find((item) => item.student.id === studentId) ?? null)
      : null;
  const outstanding = childRow?.outstanding ?? summary.outstanding;
  const overdue = childRow?.overdue ?? summary.overdue;
  const hasOverdue = apiCents(overdue) > 0;
  const noAccount = summary.account_id === null;

  const cards: {
    key: string;
    term: string;
    value: string;
    hint?: string;
    warning?: boolean;
  }[] = [
    {
      key: 'outstanding',
      term: t('familyBilling.summary.outstanding'),
      value: outstanding,
      hint: childRow
        ? t('familyBilling.summary.forChild', { name: childRow.student.name })
        : undefined,
    },
    {
      key: 'overdue',
      term: t('familyBilling.summary.overdue'),
      value: overdue,
      warning: hasOverdue,
      hint: childRow
        ? t('familyBilling.summary.forChild', { name: childRow.student.name })
        : undefined,
    },
    {
      key: 'credit',
      term: t('familyBilling.summary.accountCredit'),
      value: summary.account_credit,
      hint: childRow ? t('familyBilling.summary.wholeFamily') : undefined,
    },
    {
      key: 'net',
      term: t('familyBilling.summary.netBalance'),
      value: summary.net_balance,
      hint: childRow ? t('familyBilling.summary.wholeFamily') : undefined,
    },
  ];

  const chargesBody = stateBody(charges, () => {
    const page = (charges as { page: FamilyChargePage }).page;
    if (page.items.length === 0) {
      return (
        <p>
          {filter === 'all' && studentId === null
            ? t('familyBilling.charges.empty')
            : t('familyBilling.charges.emptyFiltered')}
        </p>
      );
    }
    return (
      <div className="flex flex-col gap-4">
        <ul className={CARD_LIST} aria-labelledby={ids.charges}>
          {page.items.map((charge) => (
            <li key={charge.id} className={CARD} data-charge-id={charge.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="min-w-0 font-medium break-words">
                  {labels.chargeLabel(charge)}
                </p>
                <ChargeStatusBadge status={charge.status} />
              </div>
              <Terms
                items={[
                  [
                    t('billing.accounts.charges.columns.serviceDate'),
                    labels.date(charge.service_date),
                  ],
                  [t('familyBilling.columns.child'), charge.student.name],
                  [
                    t('billing.accounts.charges.columns.kind'),
                    labels.chargeKind(charge.kind),
                  ],
                  [
                    t('familyBilling.charges.amount'),
                    <span key="net" data-charge-net={charge.net}>
                      {labels.money(charge.net)}
                    </span>,
                  ],
                  [
                    t('familyBilling.charges.paid'),
                    labels.money(charge.allocated),
                  ],
                  [
                    t('billing.accounts.charges.columns.open'),
                    <span key="open" data-charge-open={charge.open}>
                      {labels.money(charge.open)}
                    </span>,
                  ],
                  [
                    t('billing.accounts.charges.columns.dueDate'),
                    <DueDateCell key="due" charge={charge} touch />,
                  ],
                ]}
              />
              <div>
                <Button
                  type="button"
                  variant="outline"
                  className={TOUCH}
                  aria-label={t('familyBilling.charges.viewFor', {
                    charge: labels.chargeLabel(charge),
                    child: charge.student.name,
                    date: labels.date(charge.service_date),
                  })}
                  onClick={() => onOpenCharge(charge.id)}
                >
                  {t('familyBilling.charges.view')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <Pagination
          offset={page.offset}
          limit={page.limit}
          shown={page.items.length}
          total={page.total}
          onPage={onChargesPage}
          label={t('familyBilling.charges.pagination')}
          buttonClassName={TOUCH}
        />
      </div>
    );
  });

  const paymentsBody = stateBody(payments, () => {
    const page = (payments as { page: FamilyPaymentPage }).page;
    if (page.items.length === 0) {
      return <p>{t('familyBilling.payments.empty')}</p>;
    }
    return (
      <div className="flex flex-col gap-4">
        <ul className={CARD_LIST} aria-labelledby={ids.payments}>
          {page.items.map((payment) => (
            <li key={payment.id} className={CARD} data-payment-id={payment.id}>
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-medium">{labels.money(payment.amount)}</p>
                {payment.is_reversed || payment.reversed_at !== null ? (
                  <ReversedBadge />
                ) : null}
              </div>
              <Terms
                items={[
                  [
                    t('billing.accounts.payments.columns.receivedOn'),
                    labels.date(payment.received_on),
                  ],
                  [
                    t('billing.accounts.payments.columns.method'),
                    labels.method(payment.payment_method_code),
                  ],
                  [
                    t('familyBilling.payments.applied'),
                    labels.money(payment.allocated_amount),
                  ],
                  [
                    t('familyBilling.payments.credit'),
                    labels.money(payment.unallocated_amount),
                  ],
                  [
                    t('billing.accounts.payments.columns.reference'),
                    payment.reference ?? '—',
                  ],
                  [
                    t('billing.accounts.payments.columns.payer'),
                    payment.payer_name ?? '—',
                  ],
                  [
                    t('billing.accounts.payments.columns.receipt'),
                    <ReceiptLink
                      key="receipt"
                      familyId={summary.family.id}
                      receipt={payment.receipt}
                      linkClassName="inline-flex min-h-11 items-center"
                    />,
                  ],
                ]}
              />
              <div>
                <Button
                  type="button"
                  variant="outline"
                  className={TOUCH}
                  aria-label={t('familyBilling.payments.viewFor', {
                    amount: labels.money(payment.amount),
                    date: labels.date(payment.received_on),
                  })}
                  onClick={() => onOpenPayment(payment.id)}
                >
                  {t('familyBilling.payments.view')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <Pagination
          offset={page.offset}
          limit={page.limit}
          shown={page.items.length}
          total={page.total}
          onPage={onPaymentsPage}
          label={t('familyBilling.payments.pagination')}
          buttonClassName={TOUCH}
        />
      </div>
    );
  });

  const packagesBody = stateBody(packages, () => {
    const page = (packages as { page: FamilyPackagePage }).page;
    if (page.items.length === 0) {
      return <p>{t('familyBilling.packages.empty')}</p>;
    }
    return (
      <div className="flex flex-col gap-4">
        <ul className={CARD_LIST} aria-labelledby={ids.packages}>
          {page.items.map((pkg) => (
            <li key={pkg.id} className={CARD} data-package-id={pkg.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="min-w-0 font-medium break-words">
                  {pkg.product.name}
                </p>
                <FamilyPackageBadges pkg={pkg} today={summary.today} />
              </div>
              <Terms
                items={[
                  [t('familyBilling.columns.child'), pkg.student.name],
                  [
                    t('familyBilling.packages.subject'),
                    labels.packageSubject(pkg),
                  ],
                  [
                    t('familyBilling.packages.remaining'),
                    <span
                      key="left"
                      data-package-remaining={pkg.remaining_units}
                    >
                      {labels.packageBalance(pkg)}
                    </span>,
                  ],
                  [
                    t('familyBilling.packages.purchased'),
                    labels.date(pkg.purchased_on),
                  ],
                  [
                    t('familyBilling.packages.expires'),
                    labels.date(pkg.expires_on),
                  ],
                ]}
              />
              <div>
                <Button
                  type="button"
                  variant="outline"
                  className={TOUCH}
                  aria-label={t('familyBilling.packages.viewFor', {
                    name: pkg.product.name,
                    child: pkg.student.name,
                  })}
                  onClick={() => onOpenPackage(pkg.id)}
                >
                  {t('familyBilling.packages.view')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <Pagination
          offset={page.offset}
          limit={page.limit}
          shown={page.items.length}
          total={page.total}
          onPage={onPackagesPage}
          label={t('familyBilling.packages.pagination')}
          buttonClassName={TOUCH}
        />
      </div>
    );
  });

  const historyBody = stateBody(ledger, () => {
    const ready = ledger as Extract<LedgerState, { status: 'ready' }>;
    if (ready.items.length === 0) {
      return <p>{t('familyBilling.history.empty')}</p>;
    }
    return (
      <div className="flex flex-col items-start gap-4">
        <ol
          className="flex w-full flex-col gap-3"
          aria-labelledby={ids.history}
        >
          {ready.items.map((entry) => {
            const kind = labels.entryKind(entry);
            return (
              <li
                key={`${entry.entry_type}-${entry.entry_id}`}
                className={CARD}
                data-ledger-entry={entry.entry_type}
              >
                <p className="font-medium break-words">
                  {labels.entryType(entry)}
                  {kind ? (
                    <span className="block text-sm font-normal text-muted-foreground">
                      {kind}
                    </span>
                  ) : null}
                </p>
                <Terms
                  items={[
                    [
                      t('billing.accounts.ledger.columns.date'),
                      labels.instantDate(entry.occurred_at),
                    ],
                    [
                      t('familyBilling.columns.child'),
                      entry.student_id
                        ? (studentNames[entry.student_id] ?? '—')
                        : '—',
                    ],
                    [
                      t('billing.accounts.ledger.columns.amount'),
                      labels.money(entry.amount),
                    ],
                    [
                      t('billing.accounts.ledger.columns.effect'),
                      labels.signed(entry.balance_effect),
                    ],
                  ]}
                />
              </li>
            );
          })}
        </ol>
        {ready.hasMore ? (
          <Button
            type="button"
            variant="outline"
            className={TOUCH}
            disabled={ready.loadingMore}
            onClick={ready.onLoadMore}
          >
            {ready.loadingMore ? (
              <Spinner label={t('familyBilling.history.loading')} showLabel />
            ) : (
              t('familyBilling.history.more')
            )}
          </Button>
        ) : null}
      </div>
    );
  });

  return (
    <div className="flex w-full max-w-6xl min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold break-words">
          {t('familyBilling.title', { name: summary.family.name })}
        </h1>
        {only ? (
          <p className="text-muted-foreground">
            {t('familyBilling.onlyChild', { name: only.student.name })}
          </p>
        ) : null}
        {summary.family.is_active ? null : (
          <p data-family-inactive="">{t('familyBilling.inactive')}</p>
        )}
      </div>

      {several ? (
        <div
          role="search"
          aria-label={t('familyBilling.filters.label')}
          className="flex max-w-sm flex-col gap-2"
        >
          <label htmlFor={ids.child} className="text-sm font-medium">
            {t('familyBilling.filters.child')}
          </label>
          <select
            id={ids.child}
            className={cn(SELECT_CLASS, 'h-11')}
            value={studentId ?? ''}
            aria-describedby={ids.childHelp}
            onChange={(event) => onStudent(event.target.value || null)}
          >
            <option value="">{t('familyBilling.filters.allChildren')}</option>
            {summary.students.map((item) => (
              <option key={item.student.id} value={item.student.id}>
                {item.student.name}
              </option>
            ))}
          </select>
          <p id={ids.childHelp} className="text-sm text-muted-foreground">
            {t('familyBilling.filters.help')}
          </p>
        </div>
      ) : null}

      <section aria-labelledby={ids.summary} className="flex flex-col gap-4">
        <h2 id={ids.summary} className="text-lg font-semibold">
          {t('familyBilling.summary.title')}
        </h2>
        {noAccount ? <p>{t('familyBilling.summary.noAccount')}</p> : null}
        <dl className="grid grid-cols-1 gap-3 min-[400px]:grid-cols-2 lg:grid-cols-4">
          {cards.map((card) => (
            <div
              key={card.key}
              data-balance={card.key}
              className={cn(
                'flex min-w-0 flex-col gap-1 rounded-md border p-3',
                card.warning
                  ? 'border-destructive bg-destructive/5'
                  : 'border-border',
              )}
            >
              <dt className="flex items-center gap-1 text-sm text-muted-foreground">
                {card.warning ? (
                  <AlertTriangle
                    aria-hidden="true"
                    className="size-4 text-destructive"
                  />
                ) : null}
                {card.term}
              </dt>
              <dd className="text-lg font-semibold break-words">
                {labels.money(card.value)}
                {card.warning ? (
                  <span className="sr-only">
                    {' '}
                    {t('familyBilling.summary.overdueWarning')}
                  </span>
                ) : null}
              </dd>
              {card.hint ? (
                <dd className="text-xs text-muted-foreground">{card.hint}</dd>
              ) : null}
            </div>
          ))}
        </dl>
        <p className="text-sm text-muted-foreground">
          {t('familyBilling.summary.asOf', {
            date: labels.date(summary.today),
          })}
        </p>
        {several ? (
          <>
            <h3 id={ids.children} className="font-semibold">
              {t('familyBilling.summary.children')}
            </h3>
            <ul className={CARD_LIST} aria-labelledby={ids.children}>
              {summary.students.map((item) => (
                <li
                  key={item.student.id}
                  className={CARD}
                  data-student-balance={item.student.id}
                >
                  <p className="font-medium break-words">{item.student.name}</p>
                  <Terms
                    items={[
                      [
                        t('familyBilling.summary.outstanding'),
                        labels.money(item.outstanding),
                      ],
                      [
                        t('familyBilling.summary.overdue'),
                        labels.money(item.overdue),
                      ],
                    ]}
                  />
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>

      <section aria-labelledby={ids.charges} className="flex flex-col gap-4">
        <h2 id={ids.charges} className="text-lg font-semibold">
          {t('familyBilling.charges.title')}
        </h2>
        <div className="flex max-w-sm flex-col gap-2">
          <label htmlFor={ids.filter} className="text-sm font-medium">
            {t('familyBilling.charges.filter')}
          </label>
          <select
            id={ids.filter}
            className={cn(SELECT_CLASS, 'h-11')}
            value={filter}
            onChange={(event) => onFilter(event.target.value as ChargeFilter)}
          >
            {CHARGE_FILTERS.map((value) => (
              <option key={value} value={value}>
                {t(`familyBilling.charges.filters.${value}`)}
              </option>
            ))}
          </select>
        </div>
        {chargesBody}
      </section>

      <section aria-labelledby={ids.payments} className="flex flex-col gap-4">
        <h2 id={ids.payments} className="text-lg font-semibold">
          {t('familyBilling.payments.title')}
        </h2>
        {studentId !== null ? (
          <p className="text-sm text-muted-foreground" data-family-wide="">
            {t('familyBilling.payments.familyWide')}
          </p>
        ) : null}
        {paymentsBody}
      </section>

      <section aria-labelledby={ids.packages} className="flex flex-col gap-4">
        <h2 id={ids.packages} className="text-lg font-semibold">
          {t('familyBilling.packages.title')}
        </h2>
        {packagesBody}
      </section>

      <section aria-labelledby={ids.history} className="flex flex-col gap-4">
        <h2 id={ids.history} className="text-lg font-semibold">
          {t('familyBilling.history.title')}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t('familyBilling.history.intro')}
        </p>
        {historyBody}
      </section>
    </div>
  );
}

/** "Your family's billing account isn't set up yet" (A3), with the h1. */
export function FamilyBillingEmpty() {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-6xl min-w-0 flex-col gap-4">
      <h1 className="text-2xl font-semibold">
        {t('familyBilling.fallbackTitle')}
      </h1>
      <p role="status" data-no-family="">
        {t('familyBilling.noFamily')}
      </p>
    </div>
  );
}
