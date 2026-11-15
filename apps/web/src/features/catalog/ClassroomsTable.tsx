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
import { windowsSummary } from '@/components/weekly-windows/summary';
import type { Classroom, ClassroomPage } from './api';
import { CatalogStatusBadge } from './CatalogStatusBadge';
import { Pagination } from './Pagination';

// The classrooms table (#39 D2): name, capacity, the availability summary
// ("Lun–Vie 09:00–20:00", or "Sin restricción" without windows), status
// and an Edit button per row, then the pagination. Loading, empty and
// error states. Presentational: the page passes the query state. Below `md`
// the table scrolls inside its own container (SC 1.4.10 data-table
// exception).

export type ClassroomsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ClassroomPage };

interface ClassroomsTableProps {
  state: ClassroomsTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  onPage: (page: number) => void;
  onEdit: (classroom: Classroom) => void;
}

export function ClassroomsTable({
  state,
  labelledBy,
  onPage,
  onEdit,
}: ClassroomsTableProps) {
  const { t } = useTranslation();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('catalog.classrooms.empty')}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('catalog.classrooms.name')}</TableHead>
            <TableHead scope="col">
              {t('catalog.classrooms.capacity')}
            </TableHead>
            <TableHead scope="col">
              {t('catalog.classrooms.availability')}
            </TableHead>
            <TableHead scope="col">{t('catalog.range.status')}</TableHead>
            <TableHead scope="col">
              <span className="sr-only">{t('catalog.range.actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((classroom) => (
            <TableRow key={classroom.id}>
              <TableCell className="max-w-xs whitespace-normal break-words">
                {classroom.name}
              </TableCell>
              <TableCell>{classroom.capacity}</TableCell>
              <TableCell className="max-w-sm whitespace-normal">
                {windowsSummary(classroom.availability, t)}
              </TableCell>
              <TableCell>
                <CatalogStatusBadge active={classroom.is_active} />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={t('catalog.actions.editNamed', {
                    name: classroom.name,
                  })}
                  onClick={() => onEdit(classroom)}
                >
                  {t('catalog.actions.edit')}
                </Button>
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
