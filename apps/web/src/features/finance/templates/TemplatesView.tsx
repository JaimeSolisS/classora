import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { SELECT_CLASS } from '@/features/payroll/sheet';
import { useFormatters } from '@/lib/formatters';
import type {
  ExpenseCategory,
  ExpenseTemplate,
  TemplateListParams,
  TemplateStatus,
  Vendor,
} from '../api';
import { StateBadge, TemplateStatusBadge } from '../Badges';
import { ExpensesTabs } from '../ExpensesTabs';
import { useCategoryLabel } from '../labels';
import { TEMPLATE_STATUSES, templatePath } from './rules';
import { useNextPeriod } from './useNextPeriod';

// The recurring template list (M7-13b B), without any query: the filters
// (status, any number; category and vendor, inactive ones included), then
// the rows (name, category with its "Inactiva" badge, vendor, amount,
// frequency, next period, last generated, status), a table from `md` up and
// cards below. Rows whose refetch failed stay, with the out-of-date alert
// (B3, E2).

export type TemplatesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      templates: readonly ExpenseTemplate[];
      /** A refetch failed: the rows may be out of date. */
      refreshError?: unknown;
      onRefresh?: () => void;
    };

export type TemplateFilterChange = Partial<TemplateListParams>;

export interface TemplatesViewProps {
  state: TemplatesState;
  params: TemplateListParams;
  onFilter: (change: TemplateFilterChange) => void;
  /** Every category and vendor, for the filters; null while they load. */
  categories: readonly ExpenseCategory[] | null;
  vendors: readonly Vendor[] | null;
  filtered: boolean;
  /** "Nueva plantilla" shows (expense.manage). */
  canAdd: boolean;
  /** A refetch after a failed save hasn't landed. */
  addBlocked?: boolean;
  onAdd: () => void;
  announcement?: string;
}

const L = 'finance.templates';
const WRAP = '[overflow-wrap:anywhere]';
const LINK_CLASS = `inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6 ${WRAP}`;

export function CategoryCell({
  category,
}: {
  category: ExpenseTemplate['category'];
}) {
  const { t } = useTranslation();
  const label = useCategoryLabel();
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className={WRAP}>{label(category)}</span>
      {category.is_active ? null : (
        <StateBadge active={false}>{t(`${L}.inactiveCategory`)}</StateBadge>
      )}
    </span>
  );
}

export function VendorCell({ vendor }: { vendor: ExpenseTemplate['vendor'] }) {
  const { t } = useTranslation();
  if (!vendor) return <>{t(`${L}.none`)}</>;
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className={WRAP}>{vendor.name}</span>
      {vendor.is_active ? null : (
        <StateBadge active={false}>{t(`${L}.inactiveVendor`)}</StateBadge>
      )}
    </span>
  );
}

export function TemplatesView({
  state,
  params,
  onFilter,
  categories,
  vendors,
  filtered,
  canAdd,
  addBlocked = false,
  onAdd,
  announcement = '',
}: TemplatesViewProps) {
  const { t, i18n } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const categoryLabel = useCategoryLabel();
  const nextPeriod = useNextPeriod();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    category: `${id}-category`,
    vendor: `${id}-vendor`,
  };
  const items = state.status === 'ready' ? state.templates : [];
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
  const last = (template: ExpenseTemplate) =>
    template.last_period_start
      ? formatLocalDate(template.last_period_start)
      : t(`${L}.none`);

  function toggleStatus(status: TemplateStatus, on: boolean) {
    const next = new Set(params.statuses);
    if (on) next.add(status);
    else next.delete(status);
    onFilter({ statuses: TEMPLATE_STATUSES.filter((s) => next.has(s)) });
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
          {canAdd ? addButton : null}
        </div>
        <p className="text-sm text-muted-foreground">{t(`${L}.intro`)}</p>
        <div
          role="search"
          aria-label={t(`${L}.filters.label`)}
          className="flex min-w-0 flex-col gap-4"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
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
          </div>
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">
              {t(`${L}.filters.status`)}
            </legend>
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              {TEMPLATE_STATUSES.map((status) => (
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
        {state.status === 'loading' ? (
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
            <p data-templates-no-match>{t(`${L}.noMatch`)}</p>
          ) : (
            <div
              className="flex flex-col items-start gap-3"
              data-templates-empty
            >
              <p>{t(`${L}.empty`)}</p>
              {canAdd ? addButton : null}
            </div>
          )
        ) : null}
        {state.status === 'ready' && items.length > 0 ? (
          <>
            <div className="hidden md:block" data-templates-table>
              <Table aria-labelledby={ids.title}>
                <TableCaption className="sr-only">
                  {t(`${L}.caption`)}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    {(
                      [
                        'name',
                        'category',
                        'vendor',
                        'amount',
                        'frequency',
                        'next',
                        'last',
                        'status',
                      ] as const
                    ).map((column) => (
                      <TableHead
                        key={column}
                        scope="col"
                        className={
                          column === 'amount' ? 'text-right' : undefined
                        }
                      >
                        {t(`${L}.columns.${column}`)}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((template) => (
                    <TableRow
                      key={template.id}
                      data-template-id={template.id}
                      className="align-top"
                    >
                      <TableCell className="max-w-xs whitespace-normal">
                        <Link
                          to={templatePath(template.id)}
                          className={LINK_CLASS}
                        >
                          {template.name}
                        </Link>
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <CategoryCell category={template.category} />
                      </TableCell>
                      <TableCell className="whitespace-normal">
                        <VendorCell vendor={template.vendor} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(template.amount)}
                      </TableCell>
                      <TableCell>
                        {t(`${L}.frequencies.${template.frequency}`)}
                      </TableCell>
                      <TableCell>{nextPeriod(template)}</TableCell>
                      <TableCell>{last(template)}</TableCell>
                      <TableCell>
                        <TemplateStatusBadge status={template.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 md:hidden" data-templates-cards>
              {items.map((template) => (
                <li
                  key={template.id}
                  data-template-card={template.id}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <Link to={templatePath(template.id)} className={LINK_CLASS}>
                      {template.name}
                    </Link>
                    <TemplateStatusBadge status={template.status} />
                  </div>
                  <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.category`)}
                    </dt>
                    <dd>
                      <CategoryCell category={template.category} />
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.vendor`)}
                    </dt>
                    <dd>
                      <VendorCell vendor={template.vendor} />
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.amount`)}
                    </dt>
                    <dd className="tabular-nums">
                      {formatMoney(template.amount)}
                    </dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.frequency`)}
                    </dt>
                    <dd>{t(`${L}.frequencies.${template.frequency}`)}</dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.next`)}
                    </dt>
                    <dd>{nextPeriod(template)}</dd>
                    <dt className="text-muted-foreground">
                      {t(`${L}.columns.last`)}
                    </dt>
                    <dd>{last(template)}</dd>
                  </dl>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p
          role="status"
          aria-live="polite"
          className="text-sm"
          data-templates-announcement
        >
          {announcement}
        </p>
      </div>
    </div>
  );
}
