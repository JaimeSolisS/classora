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
import type { StudentListPage } from './api';
import { listName } from './rules';
import { StudentAgeBadge, StudentStatusBadge } from './StudentBadges';
import { useLevelLabel } from './useLevelLabel';

// The student list (#33 I3): name (a link to the detail), education level,
// grade, minor/adult and status, then Previous/Next and the range. Loading,
// empty ("no results" for a search) and error states. Presentational: the
// page passes the query state. Below `md` the table scrolls inside its own
// container (the Table component); the page itself never scrolls sideways.

export type StudentsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: StudentListPage };

interface StudentsTableProps {
  state: StudentsTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Whether a search text is applied (changes the empty message). */
  searching: boolean;
  /** Goes to another 1-based page. */
  onPage: (page: number) => void;
}

export function StudentsTable({
  state,
  labelledBy,
  searching,
  onPage,
}: StudentsTableProps) {
  const { t } = useTranslation();
  const levelLabel = useLevelLabel();

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
        {searching ? t('students.list.noResults') : t('students.list.empty')}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('students.list.name')}</TableHead>
            <TableHead scope="col">{t('students.list.level')}</TableHead>
            <TableHead scope="col">{t('students.list.grade')}</TableHead>
            <TableHead scope="col">{t('students.list.age')}</TableHead>
            <TableHead scope="col">{t('students.list.status')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <Link
                  to={`/students/${item.id}`}
                  className="inline-block min-h-6 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {listName(item)}
                </Link>
              </TableCell>
              <TableCell>{levelLabel(item.education_level)}</TableCell>
              <TableCell>{item.school_grade ?? ''}</TableCell>
              <TableCell>
                <StudentAgeBadge minor={item.is_minor} />
              </TableCell>
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
