import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Pagination } from '@/features/catalog/Pagination';
import { StudentStatusBadge } from '@/features/students/StudentBadges';
import type { GuardianListPage } from './api';
import { AccountBadge } from './GuardianBadges';
import { listName } from './rules';

// The guardian list (#34 I1): name (a link to the detail), account badge,
// number of linked students and status, then Previous/Next and the range.
// Loading, empty ("no results" for a search) and error states, as #33 I3.
// Presentational: the page passes the query state.

export type GuardiansTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: GuardianListPage };

interface GuardiansTableProps {
  state: GuardiansTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Whether a search text is applied (changes the empty message). */
  searching: boolean;
  /** Goes to another 1-based page. */
  onPage: (page: number) => void;
}

export function GuardiansTable({
  state,
  labelledBy,
  searching,
  onPage,
}: GuardiansTableProps) {
  const { t } = useTranslation();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return (
      <p>
        {searching ? t('guardians.list.noResults') : t('guardians.list.empty')}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('guardians.list.name')}</TableHead>
            <TableHead scope="col">{t('guardians.list.account')}</TableHead>
            <TableHead scope="col">{t('guardians.list.students')}</TableHead>
            <TableHead scope="col">{t('guardians.list.status')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <Link
                  to={`/guardians/${item.id}`}
                  className="inline-block min-h-6 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {listName(item)}
                </Link>
              </TableCell>
              <TableCell>
                <AccountBadge hasAccount={item.has_account} />
              </TableCell>
              <TableCell>{item.student_count}</TableCell>
              <TableCell>
                <StudentStatusBadge active={item.is_active} />
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
