import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import type {
  FamilyListPage,
  FamilyStatusFilter,
} from '@/features/families/api';
import { FamilyStatusBadge } from '@/features/families/FamilyBadges';
import { SEARCH_MAX_LENGTH } from '@/features/families/rules';
import { SELECT_CLASS } from '@/features/students/styles';
import { BillingTabs } from '../BillingTabs';

// The family accounts list (#379 B1), without any query (stories render
// each state): the Billing tabs, the title, the family name search and
// status filter (the page debounces the search and keeps both in the URL),
// and the families with their status and number of students, each linking
// to its billing account. No balance column: no list-balance API exists.

export type AccountsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: FamilyListPage };

export interface AccountsViewProps {
  state: AccountsState;
  /** The search field's text (the page applies it after a pause). */
  text: string;
  onText: (text: string) => void;
  status: FamilyStatusFilter;
  onStatus: (status: FamilyStatusFilter) => void;
  /** Whether a search is applied (changes the empty message). */
  searching: boolean;
  onPage: (page: number) => void;
}

export function AccountsView({
  state,
  text,
  onText,
  status,
  onStatus,
  searching,
  onPage,
}: AccountsViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    search: `${id}-search`,
    status: `${id}-status`,
  };

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0) {
    body = (
      <p>
        {searching
          ? t('billing.accounts.list.noResults')
          : t('billing.accounts.list.empty')}
      </p>
    );
  } else {
    const { items, total, limit, offset } = state.page;
    body = (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={ids.title}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.accounts.list.columns.family')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.list.columns.status')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.accounts.list.columns.students')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id} data-family-id={item.id}>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  <Link
                    to={`/billing/accounts/${item.id}`}
                    className="inline-block min-h-6 font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                  >
                    {item.name}
                  </Link>
                </TableCell>
                <TableCell>
                  <FamilyStatusBadge active={item.is_active} />
                </TableCell>
                <TableCell>{item.student_count}</TableCell>
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
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('billing.accounts.list.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('billing.accounts.list.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.accounts.list.filters')}
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:max-w-4xl"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.search} className="text-sm font-medium">
            {t('billing.accounts.list.search')}
          </label>
          <Input
            id={ids.search}
            type="search"
            autoComplete="off"
            spellCheck={false}
            maxLength={SEARCH_MAX_LENGTH}
            value={text}
            onChange={(event) => onText(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.status} className="text-sm font-medium">
            {t('billing.accounts.list.columns.status')}
          </label>
          <select
            id={ids.status}
            className={SELECT_CLASS}
            value={status}
            onChange={(event) =>
              onStatus(event.target.value as FamilyStatusFilter)
            }
          >
            <option value="active">{t('families.status.active')}</option>
            <option value="inactive">{t('families.status.inactive')}</option>
            <option value="all">{t('families.list.statusAll')}</option>
          </select>
        </div>
      </div>
      {body}
    </div>
  );
}
