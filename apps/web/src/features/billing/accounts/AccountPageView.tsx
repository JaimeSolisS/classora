import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { Pagination } from '@/features/catalog/Pagination';
import { FamilyStatusBadge } from '@/features/families/FamilyBadges';
import { SELECT_CLASS } from '@/features/students/styles';
import { BillingTabs } from '../BillingTabs';
import { DueDateCell } from '../due-dates/DueDateCell';
import { canChangeDueDate } from '../due-dates/rules';
import { ChargeStatusBadge } from '../tuition/TuitionBadges';
import {
  CHARGE_STATUSES,
  type AccountSummary,
  type ChargeItem,
  type ChargePage,
  type LedgerEntry,
  type PaymentPage,
} from './api';
import { ReversedBadge } from './AccountBadges';
import { apiCents } from './cents';
import { useAccountLabels } from './labels';
import { toggleStatus, type AccountView } from './rules';

// A family's billing account (#379 C), without any query (stories render
// each state): the header (family name and status, "Record payment" and
// "Apply account credit" for payment.record holders, the polite status
// that announces a recorded payment), the summary (outstanding, account
// credit, net balance, pending / partially paid / overdue, one row per
// student), the student filter shared by Charges and History (in the URL),
// then Charges (status filter, pagination), History (the ledger, newest
// first, "Load more") and Payments (a row opens its detail). Sections with
// in-page headings, not nested tabs. Below `md` each table scrolls inside
// its own container (SC 1.4.10 data-table exception). #382: the header
// shows the family's due-date offset ("Edit" for billing.manage holders),
// a moved due date carries the "changed" marker, and billing.manage holders
// get "Change due date" on each charge with something open.

export type PageState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: T };

export type LedgerState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: LedgerEntry[];
      hasMore: boolean;
      loadingMore: boolean;
      onLoadMore: () => void;
    };

export interface AccountPageViewProps {
  summary: AccountSummary;
  view: AccountView;
  onView: (view: AccountView) => void;
  charges: PageState<ChargePage>;
  ledger: LedgerState;
  payments: PageState<PaymentPage>;
  /** payment.record: "Record payment" and "Apply account credit". */
  canRecord: boolean;
  onRecord: () => void;
  onApplyCredit: () => void;
  onOpenPayment: (paymentId: string) => void;
  /** billing.manage: the offset's "Edit" and "Change due date" (#382). */
  canManage?: boolean;
  onEditOffset?: () => void;
  onChangeDueDate?: (charge: ChargeItem) => void;
  /** Read by the polite status (a recorded payment). */
  announcement?: string;
}

function stateBody<T>(
  state: PageState<T> | LedgerState,
  ready: () => ReactNode,
): ReactNode {
  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  return ready();
}

export function AccountPageView({
  summary,
  view,
  onView,
  charges,
  ledger,
  payments,
  canRecord,
  onRecord,
  onApplyCredit,
  onOpenPayment,
  canManage = false,
  onEditOffset,
  onChangeDueDate,
  announcement = '',
}: AccountPageViewProps) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const id = useId();
  const ids = {
    summary: `${id}-summary`,
    students: `${id}-students`,
    student: `${id}-student`,
    studentHelp: `${id}-student-help`,
    charges: `${id}-charges`,
    ledger: `${id}-ledger`,
    payments: `${id}-payments`,
  };
  const studentNames = Object.fromEntries(
    summary.students.map((item) => [item.student.id, item.student.name]),
  );
  const hasCredit = apiCents(summary.account_credit) > 0;
  const noAccount = summary.account_id === null;
  const chargeFiltered = view.statuses.length > 0 || view.studentId !== null;

  const totals: [string, string][] = [
    [t('billing.accounts.summary.outstanding'), summary.outstanding],
    [t('billing.accounts.summary.accountCredit'), summary.account_credit],
    [t('billing.accounts.summary.netBalance'), summary.net_balance],
    [t('billing.accounts.summary.pending'), summary.pending],
    [t('billing.accounts.summary.partiallyPaid'), summary.partially_paid],
    [t('billing.accounts.summary.overdue'), summary.overdue],
  ];

  const chargesBody = stateBody(charges, () => {
    const page = (charges as { page: ChargePage }).page;
    if (page.items.length === 0) {
      return (
        <p>
          {chargeFiltered
            ? t('billing.accounts.charges.emptyFiltered')
            : t('billing.accounts.charges.empty')}
        </p>
      );
    }
    return (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={ids.charges}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.serviceDate')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.kind')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.description')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.net')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.allocated')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.open')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.dueDate')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.charges.columns.status')}
              </TableHead>
              {canManage ? (
                <TableHead scope="col">
                  <span className="sr-only">
                    {t('billing.accounts.charges.columns.actions')}
                  </span>
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((charge) => (
              <TableRow key={charge.id} data-charge-id={charge.id}>
                <TableCell>{labels.date(charge.service_date)}</TableCell>
                <TableCell className="max-w-40 whitespace-normal break-words">
                  {charge.student.name}
                </TableCell>
                <TableCell>{labels.chargeKind(charge.kind)}</TableCell>
                <TableCell className="max-w-56 whitespace-normal break-words">
                  {charge.description ?? '—'}
                </TableCell>
                <TableCell>{labels.money(charge.net)}</TableCell>
                <TableCell>{labels.money(charge.allocated)}</TableCell>
                <TableCell>{labels.money(charge.open)}</TableCell>
                <TableCell>
                  <DueDateCell charge={charge} />
                </TableCell>
                <TableCell>
                  <ChargeStatusBadge status={charge.status} />
                </TableCell>
                {canManage ? (
                  <TableCell className="text-right">
                    {canChangeDueDate(charge) ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('billing.dueDates.change.actionFor', {
                          charge: labels.chargeLabel(charge),
                          student: charge.student.name,
                          date: labels.date(charge.service_date),
                        })}
                        onClick={() => onChangeDueDate?.(charge)}
                      >
                        {t('billing.dueDates.change.action')}
                      </Button>
                    ) : null}
                  </TableCell>
                ) : null}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Pagination
          offset={page.offset}
          limit={page.limit}
          shown={page.items.length}
          total={page.total}
          onPage={(next) => onView({ ...view, page: next })}
          label={t('billing.accounts.charges.pagination')}
        />
      </div>
    );
  });

  const ledgerBody = stateBody(ledger, () => {
    const ready = ledger as Extract<LedgerState, { status: 'ready' }>;
    if (ready.items.length === 0) {
      return <p>{t('billing.accounts.ledger.empty')}</p>;
    }
    return (
      <div className="flex flex-col items-start gap-4">
        <Table aria-labelledby={ids.ledger}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.accounts.ledger.columns.date')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.ledger.columns.type')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.ledger.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.ledger.columns.amount')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.ledger.columns.effect')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ready.items.map((entry) => {
              const kind = labels.entryKind(entry);
              return (
                <TableRow
                  key={`${entry.entry_type}-${entry.entry_id}`}
                  data-ledger-entry={entry.entry_type}
                >
                  <TableCell>{labels.instantDate(entry.occurred_at)}</TableCell>
                  <TableCell className="max-w-56 whitespace-normal break-words">
                    {labels.entryType(entry)}
                    {kind ? (
                      <span className="block text-xs text-muted-foreground">
                        {kind}
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell className="max-w-40 whitespace-normal break-words">
                    {entry.student_id
                      ? (studentNames[entry.student_id] ?? '—')
                      : '—'}
                  </TableCell>
                  <TableCell>{labels.money(entry.amount)}</TableCell>
                  <TableCell>{labels.signed(entry.balance_effect)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        {ready.hasMore ? (
          <Button
            type="button"
            variant="outline"
            disabled={ready.loadingMore}
            onClick={ready.onLoadMore}
          >
            {ready.loadingMore ? (
              <Spinner label={t('billing.accounts.ledger.loading')} showLabel />
            ) : (
              t('billing.accounts.ledger.more')
            )}
          </Button>
        ) : null}
      </div>
    );
  });

  const paymentsBody = stateBody(payments, () => {
    const page = (payments as { page: PaymentPage }).page;
    if (page.items.length === 0) {
      return <p>{t('billing.accounts.payments.empty')}</p>;
    }
    return (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={ids.payments}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.receivedOn')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.method')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.amount')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.allocated')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.unallocated')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.reference')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.payer')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.payments.columns.note')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.accounts.payments.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((payment) => (
              <TableRow key={payment.id} data-payment-id={payment.id}>
                <TableCell>{labels.date(payment.received_on)}</TableCell>
                <TableCell>
                  {labels.method(payment.payment_method_code)}
                </TableCell>
                <TableCell>
                  <span className="inline-flex flex-wrap items-center gap-2">
                    {labels.money(payment.amount)}
                    {payment.is_reversed ? <ReversedBadge /> : null}
                  </span>
                </TableCell>
                <TableCell>{labels.money(payment.allocated_amount)}</TableCell>
                <TableCell>
                  {labels.money(payment.unallocated_amount)}
                </TableCell>
                <TableCell className="max-w-40 whitespace-normal break-words">
                  {payment.reference ?? '—'}
                </TableCell>
                <TableCell className="max-w-40 whitespace-normal break-words">
                  {payment.payer_name ?? '—'}
                </TableCell>
                <TableCell className="max-w-56 whitespace-normal break-words">
                  {payment.note ?? '—'}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={t('billing.accounts.payments.viewFor', {
                      amount: labels.money(payment.amount),
                      date: labels.date(payment.received_on),
                    })}
                    onClick={() => onOpenPayment(payment.id)}
                  >
                    {t('billing.accounts.payments.view')}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Pagination
          offset={page.offset}
          limit={page.limit}
          shown={page.items.length}
          total={page.total}
          onPage={(next) => onView({ ...view, paymentsPage: next })}
          label={t('billing.accounts.payments.pagination')}
        />
      </div>
    );
  });

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-3">
        <p>
          <Link to="/billing/accounts" className={CATALOG_LINK_CLASS}>
            {t('billing.accounts.back')}
          </Link>
        </p>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold break-words">
            {t('billing.accounts.page.title', { name: summary.family.name })}
          </h1>
          {canRecord ? (
            <div className="flex flex-wrap gap-3">
              {hasCredit ? (
                <Button type="button" variant="outline" onClick={onApplyCredit}>
                  {t('billing.accounts.credit.action')}
                </Button>
              ) : null}
              <Button type="button" onClick={onRecord}>
                {t('billing.accounts.record.action')}
              </Button>
            </div>
          ) : null}
        </div>
        <p className="flex flex-wrap gap-2">
          <FamilyStatusBadge active={summary.family.is_active} />
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <p data-due-offset={summary.due_offset_days}>
            {t('billing.dueDates.offset.label', {
              count: summary.due_offset_days,
            })}
          </p>
          {canManage ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label={t('billing.dueDates.offset.editLabel')}
              onClick={onEditOffset}
            >
              {t('billing.dueDates.offset.edit')}
            </Button>
          ) : null}
        </div>
        <p role="status" className="text-sm font-medium">
          {announcement}
        </p>
      </div>

      <section aria-labelledby={ids.summary} className="flex flex-col gap-4">
        <h2 id={ids.summary} className="text-lg font-semibold">
          {t('billing.accounts.summary.title')}
        </h2>
        {noAccount ? <p>{t('billing.accounts.summary.noAccount')}</p> : null}
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:max-w-4xl">
          {totals.map(([term, value]) => (
            <div
              key={term}
              className="flex flex-col gap-1 rounded-md border border-border p-3"
            >
              <dt className="text-sm text-muted-foreground">{term}</dt>
              <dd className="text-lg font-semibold">{labels.money(value)}</dd>
            </div>
          ))}
        </dl>
        <p className="text-sm text-muted-foreground">
          {t('billing.accounts.summary.asOf', {
            date: labels.date(summary.today),
          })}
        </p>
        {summary.students.length > 0 ? (
          <>
            <h3 id={ids.students} className="font-semibold">
              {t('billing.accounts.summary.students')}
            </h3>
            <Table aria-labelledby={ids.students}>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    {t('billing.accounts.charges.columns.student')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.accounts.summary.outstanding')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('billing.accounts.summary.overdue')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {summary.students.map((item) => (
                  <TableRow
                    key={item.student.id}
                    data-student-balance={item.student.id}
                  >
                    <TableCell className="max-w-56 whitespace-normal break-words">
                      {item.student.name}
                    </TableCell>
                    <TableCell>{labels.money(item.outstanding)}</TableCell>
                    <TableCell>{labels.money(item.overdue)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : null}
      </section>

      {summary.students.length > 0 ? (
        <div
          role="search"
          aria-label={t('billing.accounts.filters.label')}
          className="flex max-w-sm flex-col gap-2"
        >
          <label htmlFor={ids.student} className="text-sm font-medium">
            {t('billing.accounts.filters.student')}
          </label>
          <select
            id={ids.student}
            className={SELECT_CLASS}
            value={view.studentId ?? ''}
            aria-describedby={ids.studentHelp}
            onChange={(event) =>
              onView({
                ...view,
                studentId: event.target.value || null,
                page: 1,
              })
            }
          >
            <option value="">
              {t('billing.accounts.filters.allStudents')}
            </option>
            {summary.students.map((item) => (
              <option key={item.student.id} value={item.student.id}>
                {item.student.name}
              </option>
            ))}
          </select>
          <p id={ids.studentHelp} className="text-sm text-muted-foreground">
            {t('billing.accounts.filters.studentHelp')}
          </p>
        </div>
      ) : null}

      <section aria-labelledby={ids.charges} className="flex flex-col gap-4">
        <h2 id={ids.charges} className="text-lg font-semibold">
          {t('billing.accounts.charges.title')}
        </h2>
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.accounts.charges.statusFilter')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {CHARGE_STATUSES.map((status) => (
              <label
                key={status}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={view.statuses.includes(status)}
                  onChange={() =>
                    onView({
                      ...view,
                      statuses: toggleStatus(view.statuses, status),
                      page: 1,
                    })
                  }
                />
                {t(`billing.tuition.chargeStatus.${status}`)}
              </label>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">
            {t('billing.accounts.charges.statusHelp')}
          </p>
        </fieldset>
        {chargesBody}
      </section>

      <section aria-labelledby={ids.ledger} className="flex flex-col gap-4">
        <h2 id={ids.ledger} className="text-lg font-semibold">
          {t('billing.accounts.ledger.title')}
        </h2>
        <p className="text-sm text-muted-foreground">
          {t('billing.accounts.ledger.intro')}
        </p>
        {ledgerBody}
      </section>

      <section aria-labelledby={ids.payments} className="flex flex-col gap-4">
        <h2 id={ids.payments} className="text-lg font-semibold">
          {t('billing.accounts.payments.title')}
        </h2>
        {paymentsBody}
      </section>
    </div>
  );
}
