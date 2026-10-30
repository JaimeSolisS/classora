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
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { BillingTabs } from '../BillingTabs';
import { searchStudents, type StudentOption } from '../tuition/api';
import {
  PACKAGE_OPTIONS_KEY,
  PACKAGE_STATUSES,
  searchFamilies,
  type NamedOption,
  type PackageListFilters,
  type PackagePage,
  type PackageStatus,
  type SoldPackage,
} from './api';
import { usePackageLabels } from './labels';
import { PackageStatusBadge } from './PackageBadges';
import { PackagesNav } from './PackagesNav';
import { isCancellable } from './rules';

// The sold package list (#377 C), without any query (stories render each
// state): the Billing tabs, the Packages sub-navigation, the title with
// "Sell package" (billing.manage only), the student and family filters
// (the #34 search picker, or the chosen one with "Show all"), the status
// checkboxes (none = every status; the page keeps every filter in the
// URL), the table and the pagination. Below `md` the table scrolls inside
// its own container (SC 1.4.10 data-table exception). #386 C1: an active
// package's row has "Cancel" for billing.manage holders, and the polite
// status reads a cancellation.

export type PackagesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: PackagePage };

export interface PackagesViewProps {
  state: PackagesState;
  filters: PackageListFilters;
  /** The filtered student's and family's names (null when unknown). */
  studentName: string | null;
  familyName: string | null;
  onStudent: (student: StudentOption | null) => void;
  onFamily: (family: NamedOption | null) => void;
  onFilters: (filters: PackageListFilters) => void;
  onPage: (page: number) => void;
  /** billing.manage: "Sell package". */
  canManage: boolean;
  /** guardian.view: the family filter. */
  canFilterFamilies: boolean;
  onSell: () => void;
  /** The list's search string, carried to the package page's back link. */
  listSearch: string;
  /** billing.manage: "Cancel" on an active package's row (#386). */
  onCancel?: (pkg: SoldPackage) => void;
  /**
   * billing.manage or finance.refund.approve: "Withdraw (unused units)" on
   * an active package's row (#387 B3).
   */
  canWithdraw?: boolean;
  onWithdraw?: (pkg: SoldPackage) => void;
  /** Read by the polite status (a cancelled package). */
  announcement?: string;
  /** Stories: the pickers' searches instead of the API. */
  searchStudent?: (q: string, signal: AbortSignal) => Promise<StudentOption[]>;
  searchFamily?: (q: string, signal: AbortSignal) => Promise<NamedOption[]>;
}

export function PackagesView({
  state,
  filters,
  studentName,
  familyName,
  onStudent,
  onFamily,
  onFilters,
  onPage,
  canManage,
  canFilterFamilies,
  onSell,
  listSearch,
  onCancel,
  canWithdraw = false,
  onWithdraw,
  announcement = '',
  searchStudent = searchStudents,
  searchFamily = searchFamilies,
}: PackagesViewProps) {
  const { t } = useTranslation();
  const labels = usePackageLabels();
  const id = useId();
  const ids = { title: `${id}-title` };

  const filtered =
    filters.statuses.length > 0 ||
    filters.studentId !== null ||
    filters.familyId !== null;

  function toggleStatus(status: PackageStatus) {
    const has = filters.statuses.includes(status);
    onFilters({
      ...filters,
      statuses: PACKAGE_STATUSES.filter((item) =>
        item === status ? !has : filters.statuses.includes(item),
      ),
      page: 1,
    });
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && filtered) {
    body = <p>{t('billing.packages.sold.emptyFiltered')}</p>;
  } else if (state.page.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.packages.sold.empty')}</p>
        {canManage ? (
          <Button type="button" variant="outline" onClick={onSell}>
            {t('billing.packages.sell.action')}
          </Button>
        ) : null}
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
                {t('billing.packages.sold.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.sold.columns.family')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.sold.columns.package')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.sold.columns.balance')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.sold.columns.purchased')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.sold.columns.expires')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.packages.sold.columns.status')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.packages.sold.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id} data-package-id={item.id}>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.student.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.family.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.product.name}
                </TableCell>
                <TableCell>{labels.balance(item)}</TableCell>
                <TableCell>{labels.date(item.purchased_on)}</TableCell>
                <TableCell>{labels.date(item.expires_on)}</TableCell>
                <TableCell>
                  <PackageStatusBadge status={item.status} />
                </TableCell>
                <TableCell className="text-right">
                  <span className="inline-flex flex-wrap justify-end gap-2">
                    {canManage && isCancellable(item) ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('billing.packages.cancel.actionFor', {
                          student: item.student.name,
                          name: item.product.name,
                        })}
                        onClick={() => onCancel?.(item)}
                      >
                        {t('billing.packages.cancel.action')}
                      </Button>
                    ) : null}
                    {canWithdraw && isCancellable(item) ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('billing.withdrawals.package.actionFor', {
                          student: item.student.name,
                          name: item.product.name,
                        })}
                        onClick={() => onWithdraw?.(item)}
                      >
                        {t('billing.withdrawals.package.action')}
                      </Button>
                    ) : null}
                    <Link
                      to={`/billing/packages/${item.id}`}
                      state={{ listSearch }}
                      className={LINK_BUTTON_CLASS}
                      aria-label={t('billing.packages.sold.viewFor', {
                        student: item.student.name,
                        name: item.product.name,
                      })}
                    >
                      {t('billing.packages.sold.view')}
                    </Link>
                  </span>
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
            {t('billing.packages.sold.title')}
          </h1>
          {canManage ? (
            <Button type="button" onClick={onSell}>
              {t('billing.packages.sell.action')}
            </Button>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.packages.sold.intro')}
        </p>
        {canManage || canWithdraw ? (
          <p className="text-sm text-muted-foreground">
            {t('billing.withdrawals.package.hint')}
          </p>
        ) : null}
        <p role="status" className="text-sm font-medium">
          {announcement}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.packages.sold.filters.label')}
        className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:max-w-5xl"
      >
        {filters.studentId ? (
          <div className="flex min-w-0 flex-col items-start gap-2">
            <p className="text-sm font-medium">
              {t('billing.packages.sold.filters.student')}
            </p>
            <p className="break-words">
              {studentName ?? t('billing.packages.sold.filters.unknown')}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onStudent(null)}
            >
              {t('billing.packages.sold.filters.clearStudent')}
            </Button>
          </div>
        ) : (
          <SearchPicker<StudentOption>
            label={t('billing.packages.sold.filters.searchStudent')}
            help={t('billing.packages.sold.filters.searchStudentHelp')}
            queryKey={[...PACKAGE_OPTIONS_KEY, 'filter-students']}
            search={searchStudent}
            optionId={(option) => option.id}
            optionLabel={(option) => option.name}
            selectedId={null}
            onSelect={(student) => onStudent(student)}
          />
        )}
        {!canFilterFamilies ? null : filters.familyId ? (
          <div className="flex min-w-0 flex-col items-start gap-2">
            <p className="text-sm font-medium">
              {t('billing.packages.sold.filters.family')}
            </p>
            <p className="break-words">
              {familyName ?? t('billing.packages.sold.filters.unknown')}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onFamily(null)}
            >
              {t('billing.packages.sold.filters.clearFamily')}
            </Button>
          </div>
        ) : (
          <SearchPicker<NamedOption>
            label={t('billing.packages.sold.filters.searchFamily')}
            help={t('billing.packages.sold.filters.searchFamilyHelp')}
            queryKey={[...PACKAGE_OPTIONS_KEY, 'filter-families']}
            search={searchFamily}
            optionId={(option) => option.id}
            optionLabel={(option) => option.name}
            selectedId={null}
            onSelect={(family) => onFamily(family)}
          />
        )}
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.packages.sold.filters.status')}
          </legend>
          {PACKAGE_STATUSES.map((status) => (
            <label
              key={status}
              className="flex min-h-6 items-center gap-2 text-sm"
            >
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={filters.statuses.includes(status)}
                onChange={() => toggleStatus(status)}
              />
              {t(`billing.packages.status.${status}`)}
            </label>
          ))}
          <p className="text-sm text-muted-foreground">
            {t('billing.packages.sold.filters.statusHelp')}
          </p>
        </fieldset>
      </div>
      {body}
    </div>
  );
}
