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
import { useFormatters } from '@/lib/formatters';
import { roleNameKey } from '../roles';
import type { UserListPage } from './api';
import { StateBadge } from './StateBadge';

// The user list (#31 J3): name (a link to the detail), username, roles,
// state and created date, then Previous/Next and the range. Loading, empty
// and error states. Presentational: the page passes the query state.
// Below `md` the table scrolls inside its own container (SC 1.4.10 data-table
// exception); the page itself never scrolls sideways.

export type UsersTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: UserListPage };

interface UsersTableProps {
  state: UsersTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Goes to another 1-based page. */
  onPage: (page: number) => void;
}

export function UsersTable({ state, labelledBy, onPage }: UsersTableProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('users.list.empty')}</p>;
  }
  const page = Math.floor(offset / limit) + 1;
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('users.list.displayName')}</TableHead>
            <TableHead scope="col">{t('users.list.username')}</TableHead>
            <TableHead scope="col">{t('users.list.roles')}</TableHead>
            <TableHead scope="col">{t('users.list.state')}</TableHead>
            <TableHead scope="col">{t('users.list.createdAt')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <Link
                  to={`/users/${item.id}`}
                  className="inline-block min-h-6 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {item.display_name}
                </Link>
              </TableCell>
              <TableCell>{item.username}</TableCell>
              <TableCell>
                {item.roles.length > 0
                  ? item.roles.map((role) => t(roleNameKey(role))).join(', ')
                  : t('users.list.noRoles')}
              </TableCell>
              <TableCell>
                <StateBadge state={item.state} />
              </TableCell>
              <TableCell>{formatInstant(item.created_at, 'date')}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <nav
        aria-label={t('users.list.pagination')}
        className="flex flex-wrap items-center gap-3"
      >
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onPage(page - 1)}
        >
          {t('users.list.previous')}
        </Button>
        <p className="text-sm">
          {t('users.list.range', {
            from: offset + 1,
            to: offset + items.length,
            total,
          })}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={offset + items.length >= total}
          onClick={() => onPage(page + 1)}
        >
          {t('users.list.next')}
        </Button>
      </nav>
    </div>
  );
}
