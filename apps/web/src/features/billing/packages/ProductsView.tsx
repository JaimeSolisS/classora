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
import type { SubjectOption } from '../pricing/api';
import type { PackageProductPage, ProductListFilters } from './api';
import { usePackageLabels } from './labels';
import { ProductActiveBadge } from './PackageBadges';
import { PackagesNav } from './PackagesNav';

// The package product list (#377 B1), without any query (stories render
// each state): the Billing tabs, the Packages sub-navigation, the title
// with "New package", the status and subject filters (the page keeps them
// in the URL), the table and the pagination. Below `md` the table scrolls
// inside its own container (SC 1.4.10 data-table exception).

export type ProductsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: PackageProductPage };

export interface ProductsViewProps {
  state: ProductsState;
  filters: ProductListFilters;
  /** The subjects of the filter (null while they load). */
  subjects: readonly SubjectOption[] | null;
  onFilters: (filters: ProductListFilters) => void;
  onPage: (page: number) => void;
  onAdd: () => void;
  /** The list's search string, carried to the product page's back link. */
  listSearch: string;
}

export function ProductsView({
  state,
  filters,
  subjects,
  onFilters,
  onPage,
  onAdd,
  listSearch,
}: ProductsViewProps) {
  const { t } = useTranslation();
  const labels = usePackageLabels();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    active: `${id}-active`,
    subject: `${id}-subject`,
  };
  const filtered = filters.isActive !== null || filters.subjectId !== null;

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && filtered) {
    body = <p>{t('billing.packages.products.emptyFiltered')}</p>;
  } else if (state.page.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.packages.products.empty')}</p>
        <Button type="button" variant="outline" onClick={onAdd}>
          {t('billing.packages.products.add')}
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
                {t('billing.packages.products.columns.name')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.products.columns.units')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.products.columns.validity')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.products.columns.subject')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.products.columns.price')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.products.columns.status')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.packages.products.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id} data-package-product-id={item.id}>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.name}
                </TableCell>
                <TableCell>{labels.units(item)}</TableCell>
                <TableCell>{labels.validity(item)}</TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {labels.subject(item)}
                </TableCell>
                <TableCell>{labels.price(item)}</TableCell>
                <TableCell>
                  <ProductActiveBadge active={item.is_active} />
                </TableCell>
                <TableCell className="text-right">
                  <Link
                    to={`/billing/package-products/${item.id}`}
                    state={{ listSearch }}
                    className={LINK_BUTTON_CLASS}
                    aria-label={t('billing.packages.products.viewFor', {
                      name: item.name,
                    })}
                  >
                    {t('billing.packages.products.view')}
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
      <PackagesNav />
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 id={ids.title} className="text-2xl font-semibold">
            {t('billing.packages.products.title')}
          </h1>
          <Button type="button" onClick={onAdd}>
            {t('billing.packages.products.add')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.packages.products.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.packages.products.filters.label')}
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:max-w-2xl"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.active} className="text-sm font-medium">
            {t('billing.packages.products.filters.status')}
          </label>
          <select
            id={ids.active}
            className={SELECT_CLASS}
            value={filters.isActive === null ? '' : String(filters.isActive)}
            onChange={(event) =>
              onFilters({
                ...filters,
                isActive:
                  event.target.value === ''
                    ? null
                    : event.target.value === 'true',
                page: 1,
              })
            }
          >
            <option value="">
              {t('billing.packages.products.filters.all')}
            </option>
            <option value="true">
              {t('billing.packages.products.active')}
            </option>
            <option value="false">
              {t('billing.packages.products.inactive')}
            </option>
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.subject} className="text-sm font-medium">
            {t('billing.packages.products.filters.subject')}
          </label>
          <select
            id={ids.subject}
            className={SELECT_CLASS}
            value={filters.subjectId ?? ''}
            disabled={subjects === null}
            onChange={(event) =>
              onFilters({
                ...filters,
                subjectId: event.target.value || null,
                page: 1,
              })
            }
          >
            <option value="">
              {t('billing.packages.products.filters.allSubjects')}
            </option>
            {filters.subjectId &&
            subjects &&
            !subjects.some((subject) => subject.id === filters.subjectId) ? (
              <option value={filters.subjectId}>
                {t('billing.packages.products.filters.otherSubject')}
              </option>
            ) : null}
            {(subjects ?? []).map((subject) => (
              <option key={subject.id} value={subject.id}>
                {t('billing.packages.subjectOption', {
                  subject: subject.name,
                  level: subject.levelName,
                })}
              </option>
            ))}
          </select>
        </div>
      </div>
      {body}
    </div>
  );
}
