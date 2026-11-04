import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
import { Pagination } from '@/features/catalog/Pagination';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { SELECT_CLASS } from '@/features/payroll/sheet';
import type { Vendor, VendorPage, VendorStateFilter } from '../api';
import { StateBadge } from '../Badges';
import { ExpensesTabs } from '../ExpensesTabs';
import { SEARCH_MAX_LENGTH, VENDOR_STATE_FILTERS } from './listParams';

// The vendors tab (M7-11b C1, C3, C4), without any query: search, the state
// filter, then name, contact (or "—") and state in the API's order (active
// first), a table from `md` up and cards below, and the shared pagination.
// A long contact wraps and never widens the page. Deactivating and
// reactivating sit behind a confirmation, which stays open with the refusal
// when `onSetActive` rejects.

export type VendorsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      page: VendorPage;
      /** A refetch failed: the rows may be out of date (#458 C2). */
      refreshError?: unknown;
      onRefresh?: () => void;
    };

export interface VendorsViewProps {
  state: VendorsState;
  /** The search box's text, as typed. */
  search: string;
  onSearch: (value: string) => void;
  stateFilter: VendorStateFilter;
  onStateFilter: (value: VendorStateFilter) => void;
  /** A search or a state filter applies: an empty page is "no match". */
  filtered: boolean;
  onPage: (page: number) => void;
  onAdd: () => void;
  onEdit: (vendor: Vendor) => void;
  /** Rejects with the error to show in the confirmation. */
  onSetActive: (vendor: Vendor, active: boolean) => Promise<void>;
  announcement?: string;
  /** Stories: the vendor whose confirmation starts open, and its refusal. */
  initialToggle?: Vendor;
  initialToggleError?: unknown;
}

const WRAP = '[overflow-wrap:anywhere]';
const V = 'finance.vendors';

function Actions({
  vendor,
  onEdit,
  onToggle,
}: {
  vendor: Vendor;
  onEdit: (vendor: Vendor) => void;
  onToggle: (vendor: Vendor) => void;
}) {
  const { t } = useTranslation();
  const name = { name: vendor.name };
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11 md:min-h-8"
        aria-label={t(`${V}.editFor`, name)}
        onClick={() => onEdit(vendor)}
      >
        {t(`${V}.edit`)}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11 md:min-h-8"
        aria-label={
          vendor.is_active
            ? t(`${V}.deactivateFor`, name)
            : t(`${V}.reactivateFor`, name)
        }
        onClick={() => onToggle(vendor)}
      >
        {vendor.is_active ? t(`${V}.deactivate`) : t(`${V}.reactivate`)}
      </Button>
    </div>
  );
}

export function VendorsView({
  state,
  search,
  onSearch,
  stateFilter,
  onStateFilter,
  filtered,
  onPage,
  onAdd,
  onEdit,
  onSetActive,
  announcement = '',
  initialToggle,
  initialToggleError = null,
}: VendorsViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = { title: `${id}-title`, search: `${id}-q`, state: `${id}-s` };
  const [toggling, setToggling] = useState<Vendor | null>(
    initialToggle ?? null,
  );
  const [toggleError, setToggleError] = useState<unknown>(initialToggleError);

  function startToggle(vendor: Vendor) {
    setToggleError(null);
    setToggling(vendor);
  }

  async function confirmToggle() {
    if (!toggling) {
      return;
    }
    setToggleError(null);
    try {
      await onSetActive(toggling, !toggling.is_active);
      setToggling(null);
    } catch (caught) {
      setToggleError(caught);
    }
  }

  const stateWord = (vendor: Vendor) =>
    t(`${V}.states.${vendor.is_active ? 'active' : 'inactive'}`);
  const contact = (vendor: Vendor) => vendor.contact ?? t(`${V}.noContact`);
  const items = state.status === 'ready' ? state.page.items : [];

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <ExpensesTabs />
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 id={ids.title} className="text-2xl font-semibold">
            {t(`${V}.title`)}
          </h1>
          <Button type="button" className="min-h-11 md:min-h-9" onClick={onAdd}>
            {t(`${V}.add`)}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">{t(`${V}.intro`)}</p>
        <div
          role="search"
          className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
        >
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={ids.search} className="text-sm font-medium">
              {t(`${V}.search`)}
            </label>
            <Input
              id={ids.search}
              type="search"
              autoComplete="off"
              spellCheck={false}
              maxLength={SEARCH_MAX_LENGTH}
              className="h-11 md:h-9"
              value={search}
              onChange={(event) => onSearch(event.target.value)}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={ids.state} className="text-sm font-medium">
              {t(`${V}.stateFilter`)}
            </label>
            <select
              id={ids.state}
              className={SELECT_CLASS}
              value={stateFilter}
              onChange={(event) =>
                onStateFilter(event.target.value as VendorStateFilter)
              }
            >
              {VENDOR_STATE_FILTERS.map((value) => (
                <option key={value} value={value}>
                  {t(`${V}.stateOptions.${value}`)}
                </option>
              ))}
            </select>
          </div>
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
            message={t(`${V}.stale`)}
            onRetry={state.onRefresh}
            retryLabel={t('finance.reloadList')}
          />
        ) : null}
        {state.status === 'ready' && items.length === 0 ? (
          filtered ? (
            <p data-vendors-no-match>{t(`${V}.noMatch`)}</p>
          ) : (
            <div className="flex flex-col items-start gap-3" data-vendors-empty>
              <p>{t(`${V}.empty`)}</p>
              <Button
                type="button"
                variant="outline"
                className="min-h-11 md:min-h-9"
                onClick={onAdd}
              >
                {t(`${V}.add`)}
              </Button>
            </div>
          )
        ) : null}
        {state.status === 'ready' && items.length > 0 ? (
          <div className="flex flex-col gap-4">
            <div className="hidden md:block" data-vendors-table>
              <Table aria-labelledby={ids.title}>
                <TableCaption className="sr-only">
                  {t(`${V}.caption`)}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">{t(`${V}.columns.name`)}</TableHead>
                    <TableHead scope="col">
                      {t(`${V}.columns.contact`)}
                    </TableHead>
                    <TableHead scope="col">{t(`${V}.columns.state`)}</TableHead>
                    <TableHead scope="col">
                      {t(`${V}.columns.actions`)}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((vendor) => (
                    <TableRow
                      key={vendor.id}
                      data-vendor-id={vendor.id}
                      className="align-top"
                    >
                      <TableCell className={`whitespace-normal ${WRAP}`}>
                        {vendor.name}
                      </TableCell>
                      <TableCell
                        className={`max-w-xs whitespace-pre-line ${WRAP}`}
                      >
                        {contact(vendor)}
                      </TableCell>
                      <TableCell>
                        <StateBadge active={vendor.is_active}>
                          {stateWord(vendor)}
                        </StateBadge>
                      </TableCell>
                      <TableCell>
                        <Actions
                          vendor={vendor}
                          onEdit={onEdit}
                          onToggle={startToggle}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 md:hidden" data-vendors-cards>
              {items.map((vendor) => (
                <li
                  key={vendor.id}
                  data-vendor-card={vendor.id}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
                >
                  <p className={`font-medium ${WRAP}`}>{vendor.name}</p>
                  <p className={`text-sm whitespace-pre-line ${WRAP}`}>
                    {contact(vendor)}
                  </p>
                  <span>
                    <StateBadge active={vendor.is_active}>
                      {stateWord(vendor)}
                    </StateBadge>
                  </span>
                  <Actions
                    vendor={vendor}
                    onEdit={onEdit}
                    onToggle={startToggle}
                  />
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
        <p role="status" aria-live="polite" className="text-sm">
          {announcement}
        </p>
      </div>
      <ConfirmDialog
        open={toggling !== null}
        title={
          toggling?.is_active
            ? t(`${V}.toggle.deactivateTitle`, { name: toggling.name })
            : t(`${V}.toggle.reactivateTitle`, { name: toggling?.name ?? '' })
        }
        body={
          toggling?.is_active
            ? t(`${V}.toggle.deactivateBody`)
            : t(`${V}.toggle.reactivateBody`)
        }
        confirmLabel={
          toggling?.is_active ? t(`${V}.deactivate`) : t(`${V}.reactivate`)
        }
        destructive={toggling?.is_active === true}
        alert={toggleError ? <ErrorAlert error={toggleError} /> : null}
        onConfirm={confirmToggle}
        onCancel={() => {
          setToggling(null);
          setToggleError(null);
        }}
      />
    </div>
  );
}
