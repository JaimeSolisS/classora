import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Pagination } from '@/features/catalog/Pagination';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import { BillingTabs } from '../BillingTabs';
import type {
  ClassType,
  PriceListFilters,
  PricingModel,
  ServicePricePage,
} from './api';
import { usePriceLabels } from './labels';
import { CLASS_TYPES, PRICING_MODELS } from './rules';

// The price list (#363 B), without any query (stories render each state):
// the Billing tabs, the title with "Add price", the class type and pricing
// model filters (the page keeps them in the URL) and the table with the
// price in effect today and the next change, then the pagination. Below
// `md` the table scrolls inside its own container (SC 1.4.10 data-table
// exception).

export type PricesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ServicePricePage };

export interface PricesViewProps {
  state: PricesState;
  filters: PriceListFilters;
  onFilters: (filters: PriceListFilters) => void;
  onPage: (page: number) => void;
  onAdd: () => void;
  /** The list's search string, carried to the detail page's back link. */
  listSearch: string;
}

export function PricesView({
  state,
  filters,
  onFilters,
  onPage,
  onAdd,
  listSearch,
}: PricesViewProps) {
  const { t } = useTranslation();
  const labels = usePriceLabels();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    classType: `${id}-class-type`,
    pricingModel: `${id}-pricing-model`,
  };

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (
    state.page.items.length === 0 &&
    (filters.classType || filters.pricingModel)
  ) {
    body = <p>{t('billing.prices.emptyFiltered')}</p>;
  } else if (state.page.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.prices.empty')}</p>
        <Button type="button" variant="outline" onClick={onAdd}>
          {t('billing.prices.add')}
        </Button>
      </div>
    );
  } else {
    const { items, total, limit, offset } = state.page;
    body = (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={ids.title}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.prices.columns.service')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.prices.columns.subject')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.prices.columns.level')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.prices.columns.duration')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.prices.columns.scope')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.prices.columns.current')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.prices.columns.next')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.prices.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const subject = labels.subject(item);
              return (
                <TableRow key={item.id} data-price-id={item.id}>
                  <TableCell>{labels.service(item)}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal break-words">
                    {subject}
                  </TableCell>
                  <TableCell>{labels.level(item)}</TableCell>
                  <TableCell>{labels.duration(item)}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal break-words">
                    {labels.scope(item)}
                  </TableCell>
                  <TableCell>{labels.current(item)}</TableCell>
                  <TableCell>{labels.next(item)}</TableCell>
                  <TableCell className="text-right">
                    <Link
                      to={`/billing/prices/${item.id}`}
                      state={{ listSearch }}
                      className={LINK_BUTTON_CLASS}
                      aria-label={t('billing.prices.viewFor', {
                        service: labels.service(item),
                        subject,
                      })}
                    >
                      {t('billing.prices.view')}
                    </Link>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        <Pagination
          offset={offset}
          limit={limit}
          shown={items.length}
          total={total}
          onPage={onPage}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 id={ids.title} className="text-2xl font-semibold">
            {t('billing.prices.title')}
          </h1>
          <Button type="button" onClick={onAdd}>
            {t('billing.prices.add')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.prices.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.prices.filters.label')}
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:max-w-2xl"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.classType} className="text-sm font-medium">
            {t('billing.prices.fields.classType')}
          </label>
          <select
            id={ids.classType}
            className={SELECT_CLASS}
            value={filters.classType ?? ''}
            onChange={(event) =>
              onFilters({
                ...filters,
                classType:
                  CLASS_TYPES.find((type) => type === event.target.value) ??
                  null,
                page: 1,
              })
            }
          >
            <option value="">{t('billing.prices.filters.all')}</option>
            {CLASS_TYPES.map((type: ClassType) => (
              <option key={type} value={type}>
                {labels.classType(type)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.pricingModel} className="text-sm font-medium">
            {t('billing.prices.fields.pricingModel')}
          </label>
          <select
            id={ids.pricingModel}
            className={SELECT_CLASS}
            value={filters.pricingModel ?? ''}
            onChange={(event) =>
              onFilters({
                ...filters,
                pricingModel:
                  PRICING_MODELS.find(
                    (model) => model === event.target.value,
                  ) ?? null,
                page: 1,
              })
            }
          >
            <option value="">{t('billing.prices.filters.all')}</option>
            {PRICING_MODELS.map((model: PricingModel) => (
              <option key={model} value={model}>
                {t(`billing.prices.modelFilters.${model}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {body}
    </div>
  );
}
