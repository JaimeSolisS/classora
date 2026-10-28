import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
import { SELECT_CLASS } from '@/features/students/styles';
import { formatMoney } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import { BillingTabs } from '../BillingTabs';
import { PriceStatusBadge } from '../pricing/PriceVersionsTable';
import { TargetPicker, type TargetPickerProps } from '../TargetPicker';
import type { TargetKind, TargetOption } from '../targets';
import type {
  CustomPrice,
  CustomPriceListFilters,
  CustomPricePage,
} from './api';
import { useCustomPriceLabels } from './labels';
import { canDelete, canEnd, STATUS_CHOICES } from './rules';

// The custom price list (#367 E), without any query (stories render each
// state): the Billing tabs, the title with "Add custom price" and the
// one-line rule (#95 H5, H6), the student or family filter and the status
// filter (the page keeps them in the URL), the table and the pagination.
// "End" shows on current and upcoming prices that don't already end today;
// "Delete" only on upcoming prices no charge uses. Below `md` the table
// scrolls inside its own container (SC 1.4.10 data-table exception).

export type CustomPricesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: CustomPricePage };

export interface CustomPricesViewProps {
  state: CustomPricesState;
  filters: CustomPriceListFilters;
  /** The filtered student or family with its name (null when unknown). */
  targetName: string | null;
  onTarget: (target: TargetOption | null) => void;
  onFilters: (filters: CustomPriceListFilters) => void;
  onPage: (page: number) => void;
  onAdd: () => void;
  onEnd: (item: CustomPrice) => void;
  onDelete: (item: CustomPrice) => void;
  today: LocalDate;
  /** The last change, announced politely. */
  announcement?: string;
  /** Stories: the picker's search instead of the API. */
  search?: TargetPickerProps['search'];
}

export function CustomPricesView({
  state,
  filters,
  targetName,
  onTarget,
  onFilters,
  onPage,
  onAdd,
  onEnd,
  onDelete,
  today,
  announcement = '',
  search,
}: CustomPricesViewProps) {
  const { t } = useTranslation();
  const labels = useCustomPriceLabels();
  const id = useId();
  const ids = { title: `${id}-title`, status: `${id}-status` };
  const [kind, setKind] = useState<TargetKind>('student');

  const filtered = filters.target !== null || filters.status !== 'open';

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && filtered) {
    body = <p>{t('billing.customPrices.emptyFiltered')}</p>;
  } else if (state.page.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.customPrices.empty')}</p>
        <Button type="button" variant="outline" onClick={onAdd}>
          {t('billing.customPrices.add')}
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
                {t('billing.customPrices.columns.target')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.customPrices.columns.service')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.customPrices.columns.catalog')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.customPrices.columns.amount')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.customPrices.columns.dates')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.customPrices.columns.status')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.customPrices.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const target = labels.target(item);
              const service = labels.service(item.service_price);
              return (
                <TableRow key={item.id} data-custom-price-id={item.id}>
                  <TableCell className="max-w-xs whitespace-normal break-words">
                    <span className="flex flex-col">
                      <span className="font-medium">{target.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {t(`billing.targets.kinds.${target.kind}`)}
                      </span>
                    </span>
                  </TableCell>
                  <TableCell className="max-w-xs whitespace-normal break-words">
                    {service}
                  </TableCell>
                  <TableCell>{labels.catalog(item.service_price)}</TableCell>
                  <TableCell>{formatMoney(item.amount)}</TableCell>
                  <TableCell>{labels.dates(item)}</TableCell>
                  <TableCell>
                    <PriceStatusBadge status={item.status} />
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      {canEnd(item, today) ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={t('billing.customPrices.endFor', {
                            name: target.name,
                            service,
                          })}
                          onClick={() => onEnd(item)}
                        >
                          {t('billing.customPrices.end')}
                        </Button>
                      ) : null}
                      {canDelete(item) ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={t('billing.customPrices.deleteFor', {
                            name: target.name,
                            service,
                          })}
                          onClick={() => onDelete(item)}
                        >
                          {t('billing.customPrices.delete')}
                        </Button>
                      ) : null}
                    </div>
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
            {t('billing.customPrices.title')}
          </h1>
          <Button type="button" onClick={onAdd}>
            {t('billing.customPrices.add')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.customPrices.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.customPrices.filters.label')}
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:max-w-3xl"
      >
        {filters.target ? (
          <div className="flex min-w-0 flex-col items-start gap-2">
            <p className="text-sm font-medium">
              {t(`billing.targets.kinds.${filters.target.kind}`)}
            </p>
            <p className="break-words">
              {targetName ??
                t(
                  `billing.customPrices.filters.unknown.${filters.target.kind}`,
                )}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onTarget(null)}
            >
              {t('billing.customPrices.filters.clear')}
            </Button>
          </div>
        ) : (
          <TargetPicker
            legend={t('billing.customPrices.filters.target')}
            kind={kind}
            onKind={setKind}
            selected={null}
            onSelect={(target) => onTarget(target)}
            search={search}
          />
        )}
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.status} className="text-sm font-medium">
            {t('billing.customPrices.filters.status')}
          </label>
          <select
            id={ids.status}
            className={SELECT_CLASS}
            value={filters.status}
            onChange={(event) =>
              onFilters({
                ...filters,
                status:
                  STATUS_CHOICES.find(
                    (choice) => choice === event.target.value,
                  ) ?? 'open',
                page: 1,
              })
            }
          >
            {STATUS_CHOICES.map((choice) => (
              <option key={choice} value={choice}>
                {t(`billing.customPrices.statusFilters.${choice}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {body}
    </div>
  );
}
