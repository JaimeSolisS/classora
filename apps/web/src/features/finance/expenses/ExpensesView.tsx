import { Repeat } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { FieldError } from '@/features/auth/FieldError';
import { Pagination } from '@/features/catalog/Pagination';
import { INPUT_CLASS, SELECT_CLASS } from '@/features/payroll/sheet';
import { useFormatters } from '@/lib/formatters';
import type {
  ExpenseCategory,
  ExpenseItem,
  ExpenseListParams,
  ExpensePage,
  ExpenseSort,
  ExpenseStatus,
  Vendor,
} from '../api';
import { ExpenseStatusBadge, StateBadge } from '../Badges';
import { ExpensesTabs } from '../ExpensesTabs';
import { useCategoryLabel } from '../labels';
import {
  EXPENSE_SORTS,
  EXPENSE_STATUSES,
  expensePath,
  SEARCH_MAX,
} from './rules';

// The expense list (M7-12b B), without any query: the filters (status, any
// number; category and vendor, inactive ones included; the expense-date
// range; the search; the sort), then the rows (date, category with its
// "Inactiva" badge, vendor, description, amount, open, due date, status), a
// table from `md` up and cards below, and the shared pagination. Rows whose
// refetch failed stay, with the out-of-date alert (B3).

export type ExpensesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      page: ExpensePage;
      /** A refetch failed: the rows may be out of date. */
      refreshError?: unknown;
      onRefresh?: () => void;
    };

/** The filters a select or a box changes at once (not the search). */
export type ExpenseFilterChange = Partial<
  Pick<
    ExpenseListParams,
    'statuses' | 'categoryId' | 'vendorId' | 'from' | 'to' | 'sort'
  >
>;

export interface ExpensesViewProps {
  state: ExpensesState;
  params: ExpenseListParams;
  /** The search box's text, as typed. */
  search: string;
  onSearch: (value: string) => void;
  onFilter: (change: ExpenseFilterChange) => void;
  /** Every category and vendor, for the filters; null while they load. */
  categories: readonly ExpenseCategory[] | null;
  vendors: readonly Vendor[] | null;
  /** `to` before `from`: nothing is asked. */
  rangeInvalid: boolean;
  filtered: boolean;
  onPage: (page: number) => void;
  /** "Nuevo gasto" shows (expense.manage). */
  canAdd: boolean;
  /** A refetch after a failed attempt hasn't landed. */
  addBlocked?: boolean;
  onAdd: () => void;
  announcement?: string;
  /** M8-07b A3: the page's "Export" link, when the user may export. */
  exportLink?: ReactNode;
}

const L = 'finance.expenses';
const WRAP = '[overflow-wrap:anywhere]';
const LINK_CLASS = `inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6 ${WRAP}`;

/** A generated expense's marker (M7-13b E1): an icon with its label. */
function RecurringMarker({ expense }: { expense: ExpenseItem }) {
  const { t } = useTranslation();
  if (!expense.template) return null;
  const label = t('finance.templates.marker');
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className="ml-1 inline-flex shrink-0 align-middle text-muted-foreground"
      data-recurring-marker
    >
      <Repeat aria-hidden="true" className="size-4" />
    </span>
  );
}

function CategoryCell({ expense }: { expense: ExpenseItem }) {
  const { t } = useTranslation();
  const label = useCategoryLabel();
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className={WRAP}>{label(expense.category)}</span>
      {expense.category.is_active ? null : (
        <StateBadge active={false}>{t(`${L}.inactiveCategory`)}</StateBadge>
      )}
    </span>
  );
}

function VendorCell({ expense }: { expense: ExpenseItem }) {
  const { t } = useTranslation();
  if (!expense.vendor) return <>{t(`${L}.none`)}</>;
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className={WRAP}>{expense.vendor.name}</span>
      {expense.vendor.is_active ? null : (
        <StateBadge active={false}>{t(`${L}.inactiveVendor`)}</StateBadge>
      )}
    </span>
  );
}

export function ExpensesView({
  state,
  params,
  search,
  onSearch,
  onFilter,
  categories,
  vendors,
  rangeInvalid,
  filtered,
  onPage,
  canAdd,
  addBlocked = false,
  onAdd,
  announcement = '',
  exportLink = null,
}: ExpensesViewProps) {
  const { t, i18n } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const categoryLabel = useCategoryLabel();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    q: `${id}-q`,
    category: `${id}-category`,
    vendor: `${id}-vendor`,
    from: `${id}-from`,
    to: `${id}-to`,
    toError: `${id}-to-error`,
    sort: `${id}-sort`,
  };
  const items = state.status === 'ready' ? state.page.items : [];
  const collator = new Intl.Collator(i18n.resolvedLanguage);
  const categoryOptions = (categories ?? [])
    .map((category) => ({
      id: category.id,
      label: categoryLabel(category),
      active: category.is_active,
    }))
    .sort(
      (a, b) =>
        Number(b.active) - Number(a.active) ||
        collator.compare(a.label, b.label),
    );
  const optionText = (label: string, active: boolean) =>
    active ? label : t(`${L}.filters.inactiveOption`, { name: label });

  function toggleStatus(status: ExpenseStatus, on: boolean) {
    const next = new Set(params.statuses);
    if (on) next.add(status);
    else next.delete(status);
    onFilter({ statuses: EXPENSE_STATUSES.filter((s) => next.has(s)) });
  }

  const addButton = (
    <Button
      type="button"
      className="min-h-11 md:min-h-9"
      disabled={addBlocked}
      onClick={onAdd}
    >
      {t(`${L}.add`)}
    </Button>
  );

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <ExpensesTabs />
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 id={ids.title} className="text-2xl font-semibold">
            {t(`${L}.title`)}
          </h1>
          <div className="flex flex-wrap items-center gap-2 empty:hidden">
            {exportLink}
            {canAdd ? addButton : null}
          </div>
        </div>
        <p className="text-sm text-muted-foreground">{t(`${L}.intro`)}</p>
        <div
          role="search"
          aria-label={t(`${L}.filters.label`)}
          className="flex min-w-0 flex-col gap-4"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="flex min-w-0 flex-col gap-2 md:col-span-3">
              <label htmlFor={ids.q} className="text-sm font-medium">
                {t(`${L}.filters.search`)}
              </label>
              <Input
                id={ids.q}
                type="search"
                autoComplete="off"
                spellCheck={false}
                maxLength={SEARCH_MAX}
                className={INPUT_CLASS}
                value={search}
                onChange={(event) => onSearch(event.target.value)}
              />
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={ids.category} className="text-sm font-medium">
                {t(`${L}.filters.category`)}
              </label>
              <select
                id={ids.category}
                className={SELECT_CLASS}
                value={params.categoryId}
                onChange={(event) =>
                  onFilter({ categoryId: event.target.value })
                }
              >
                <option value="">{t(`${L}.filters.all`)}</option>
                {categoryOptions.map((category) => (
                  <option key={category.id} value={category.id}>
                    {optionText(category.label, category.active)}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={ids.vendor} className="text-sm font-medium">
                {t(`${L}.filters.vendor`)}
              </label>
              <select
                id={ids.vendor}
                className={SELECT_CLASS}
                value={params.vendorId}
                onChange={(event) => onFilter({ vendorId: event.target.value })}
              >
                <option value="">{t(`${L}.filters.all`)}</option>
                {(vendors ?? []).map((vendor) => (
                  <option key={vendor.id} value={vendor.id}>
                    {optionText(vendor.name, vendor.is_active)}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={ids.sort} className="text-sm font-medium">
                {t(`${L}.filters.sort`)}
              </label>
              <select
                id={ids.sort}
                className={SELECT_CLASS}
                value={params.sort}
                onChange={(event) =>
                  onFilter({ sort: event.target.value as ExpenseSort })
                }
              >
                {EXPENSE_SORTS.map((sort) => (
                  <option key={sort} value={sort}>
                    {t(
                      `${L}.filters.sorts.${sort === 'due_on' ? 'due' : 'incurred'}`,
                    )}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={ids.from} className="text-sm font-medium">
                {t(`${L}.filters.from`)}
              </label>
              <Input
                id={ids.from}
                type="date"
                className={INPUT_CLASS}
                value={params.from}
                onChange={(event) => onFilter({ from: event.target.value })}
              />
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={ids.to} className="text-sm font-medium">
                {t(`${L}.filters.to`)}
              </label>
              <Input
                id={ids.to}
                type="date"
                className={INPUT_CLASS}
                value={params.to}
                aria-invalid={rangeInvalid ? true : undefined}
                aria-describedby={rangeInvalid ? ids.toError : undefined}
                onChange={(event) => onFilter({ to: event.target.value })}
              />
              <FieldError
                id={ids.toError}
                message={
                  rangeInvalid
                    ? t('range_invalid', { ns: 'errors' })
                    : undefined
                }
              />
            </div>
          </div>
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">
              {t(`${L}.filters.status`)}
            </legend>
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              {EXPENSE_STATUSES.map((status) => (
                <label
                  key={status}
                  className="flex min-h-11 items-center gap-2 text-sm md:min-h-6"
                >
                  <input
                    type="checkbox"
                    className="size-4 accent-primary"
                    checked={params.statuses.includes(status)}
                    onChange={(event) =>
                      toggleStatus(status, event.target.checked)
                    }
                  />
                  {t(`${L}.statuses.${status}`)}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        {state.status === 'loading' && !rangeInvalid ? (
          <LoadingState layout="table" rows={5} />
        ) : null}
        {state.status === 'error' ? (
          <ErrorAlert error={state.error} onRetry={state.onRetry} />
        ) : null}
        {state.status === 'ready' && state.refreshError ? (
          <ErrorAlert
            error={state.refreshError}
            message={t(`${L}.stale`)}
            onRetry={state.onRefresh}
            retryLabel={t('finance.reloadList')}
          />
        ) : null}
        {state.status === 'ready' && items.length === 0 ? (
          filtered ? (
            <p data-expenses-no-match>{t(`${L}.noMatch`)}</p>
          ) : (
            <div
              className="flex flex-col items-start gap-3"
              data-expenses-empty
            >
              <p>{t(`${L}.empty`)}</p>
              {canAdd ? addButton : null}
            </div>
          )
        ) : null}
        {state.status === 'ready' && items.length > 0 ? (
          <div className="flex flex-col gap-4">
            <div className="hidden md:block" data-expenses-table>
              <Table aria-labelledby={ids.title}>
                <TableCaption className="sr-only">
                  {t(`${L}.caption`)}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    {(
                      [
                        'incurredOn',
                        'category',
                        'vendor',
                        'description',
                        'net',
                        'open',
                        'dueOn',
                        'status',
                      ] as const
                    ).map((column) => (
                      <TableHead
                        key={column}
                        scope="col"
                        className={
                          column === 'net' || column === 'open'
                            ? 'text-right'
                            : undefined
                        }
                      >
                        {t(`${L}.columns.${column}`)}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((expense) => (
                    <TableRow
                      key={expense.id}
                      data-expense-id={expense.id}
                      className="align-top"
                    >
                      <TableCell>
                        {formatLocalDate(expense.incurred_on)}
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <CategoryCell expense={expense} />
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <VendorCell expense={expense} />
                      </TableCell>
                      <TableCell className="max-w-xs whitespace-normal">
                        <Link
                          to={expensePath(expense.id)}
                          className={LINK_CLASS}
                        >
                          {expense.description}
                        </Link>
                        <RecurringMarker expense={expense} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(expense.adjusted_amount)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(expense.open_amount)}
                      </TableCell>
                      <TableCell>{formatLocalDate(expense.due_on)}</TableCell>
                      <TableCell>
                        <ExpenseStatusBadge status={expense.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 md:hidden" data-expenses-cards>
              {items.map((expense) => (
                <li
                  key={expense.id}
                  data-expense-card={expense.id}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <span className="flex min-w-0 items-center">
                      <Link to={expensePath(expense.id)} className={LINK_CLASS}>
                        {expense.description}
                      </Link>
                      <RecurringMarker expense={expense} />
                    </span>
                    <ExpenseStatusBadge status={expense.status} />
                  </div>
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.incurredOn`)}
                    </dt>
                    <dd>{formatLocalDate(expense.incurred_on)}</dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.category`)}
                    </dt>
                    <dd>
                      <CategoryCell expense={expense} />
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.vendor`)}
                    </dt>
                    <dd>
                      <VendorCell expense={expense} />
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.net`)}
                    </dt>
                    <dd className="tabular-nums">
                      {formatMoney(expense.adjusted_amount)}
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.open`)}
                    </dt>
                    <dd className="tabular-nums">
                      {formatMoney(expense.open_amount)}
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.dueOn`)}
                    </dt>
                    <dd>{formatLocalDate(expense.due_on)}</dd>
                  </dl>
                </li>
              ))}
            </ul>
            <Pagination
              offset={state.page.offset}
              limit={state.page.limit}
              shown={items.length}
              total={state.page.total}
              onPage={onPage}
              buttonClassName="min-h-11 md:min-h-9"
            />
          </div>
        ) : null}
        <p
          role="status"
          aria-live="polite"
          className="text-sm"
          data-expenses-announcement
        >
          {announcement}
        </p>
      </div>
    </div>
  );
}
