import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import type { DiscountListFilters, DiscountPage } from './api';
import { DiscountKindBadge, DiscountStatusBadge } from './DiscountBadges';
import { useDiscountLabels } from './labels';
import { DISCOUNT_KINDS, Q_MAX_LENGTH, STATUS_CHOICES } from './rules';

// The discount list (#367 B), without any query (stories render each
// state): the Billing tabs, the title with "Add discount" and the one-line
// rule (#95 H4), the kind, status and name filters (the page keeps them in
// the URL; typing waits 300 ms), the table in the server's order and the
// pagination. Below `md` the table scrolls inside its own container
// (SC 1.4.10 data-table exception).

/** How long typing must pause before the name search runs. */
export const SEARCH_DEBOUNCE_MS = 300;

export type DiscountsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: DiscountPage };

export interface DiscountsViewProps {
  state: DiscountsState;
  filters: DiscountListFilters;
  onFilters: (filters: DiscountListFilters) => void;
  onPage: (page: number) => void;
  onAdd: () => void;
  /** The list's search string, carried to the detail page's back link. */
  listSearch: string;
}

export function DiscountsView({
  state,
  filters,
  onFilters,
  onPage,
  onAdd,
  listSearch,
}: DiscountsViewProps) {
  const { t } = useTranslation();
  const labels = useDiscountLabels();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    kind: `${id}-kind`,
    status: `${id}-status`,
    q: `${id}-q`,
  };
  const [text, setText] = useState(filters.q);
  const [shownQ, setShownQ] = useState(filters.q);
  if (filters.q !== shownQ) {
    // Back/forward or a link changed q: show it in the field.
    setShownQ(filters.q);
    if (text.trim() !== filters.q) {
      setText(filters.q);
    }
  }

  useEffect(() => {
    const q = text.trim();
    if (q === filters.q) {
      return;
    }
    const timer = window.setTimeout(() => {
      onFilters({ ...filters, q, page: 1 });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [text, filters, onFilters]);

  // The default status filter (current and upcoming) is the plain list.
  const filtered =
    filters.kind !== null || filters.status !== 'open' || filters.q !== '';

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && filtered) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.discounts.emptyFiltered')}</p>
      </div>
    );
  } else if (state.page.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.discounts.empty')}</p>
        <Button type="button" variant="outline" onClick={onAdd}>
          {t('billing.discounts.add')}
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
                {t('billing.discounts.columns.name')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.columns.kind')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.columns.value')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.columns.who')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.columns.criteria')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.columns.validity')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.columns.status')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.discounts.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id} data-discount-id={item.id}>
                <TableCell className="max-w-xs font-medium whitespace-normal break-words">
                  {item.name}
                </TableCell>
                <TableCell>
                  <DiscountKindBadge kind={item.kind} />
                </TableCell>
                <TableCell>{labels.value(item)}</TableCell>
                <TableCell>{labels.who(item)}</TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {labels.criteria(item)}
                </TableCell>
                <TableCell>{labels.validity(item)}</TableCell>
                <TableCell>
                  <DiscountStatusBadge status={item.status} />
                </TableCell>
                <TableCell className="text-right">
                  <Link
                    to={`/billing/discounts/${item.id}`}
                    state={{ listSearch }}
                    className={LINK_BUTTON_CLASS}
                    aria-label={t('billing.discounts.viewFor', {
                      name: item.name,
                    })}
                  >
                    {t('billing.discounts.view')}
                  </Link>
                </TableCell>
              </TableRow>
            ))}
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
            {t('billing.discounts.title')}
          </h1>
          <Button type="button" onClick={onAdd}>
            {t('billing.discounts.add')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.discounts.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.discounts.filters.label')}
        className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:max-w-3xl"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.q} className="text-sm font-medium">
            {t('billing.discounts.filters.q')}
          </label>
          <Input
            id={ids.q}
            type="search"
            autoComplete="off"
            spellCheck={false}
            maxLength={Q_MAX_LENGTH}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.kind} className="text-sm font-medium">
            {t('billing.discounts.fields.kind')}
          </label>
          <select
            id={ids.kind}
            className={SELECT_CLASS}
            value={filters.kind ?? ''}
            onChange={(event) =>
              onFilters({
                ...filters,
                kind:
                  DISCOUNT_KINDS.find((kind) => kind === event.target.value) ??
                  null,
                page: 1,
              })
            }
          >
            <option value="">{t('billing.discounts.filters.allKinds')}</option>
            {DISCOUNT_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`billing.discounts.kinds.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.status} className="text-sm font-medium">
            {t('billing.discounts.filters.status')}
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
                {t(`billing.discounts.statusFilters.${choice}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {body}
    </div>
  );
}
