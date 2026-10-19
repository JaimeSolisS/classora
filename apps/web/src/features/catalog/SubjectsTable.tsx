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
import type { Subject, SubjectPage } from './api';
import { CatalogStatusBadge } from './CatalogStatusBadge';
import { Pagination } from './Pagination';

// The subjects of one level (#32 I2): name, status and an Edit button per
// row, then the pagination. Loading, empty and error states.

export type SubjectsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: SubjectPage };

interface SubjectsTableProps {
  state: SubjectsTableState;
  labelledBy: string;
  onPage: (page: number) => void;
  onEdit: (subject: Subject) => void;
}

export function SubjectsTable({
  state,
  labelledBy,
  onPage,
  onEdit,
}: SubjectsTableProps) {
  const { t } = useTranslation();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('catalog.subjects.empty')}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('catalog.levels.name')}</TableHead>
            <TableHead scope="col">{t('catalog.levels.status')}</TableHead>
            <TableHead scope="col">
              <span className="sr-only">{t('catalog.levels.actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((subject) => (
            <TableRow key={subject.id}>
              <TableCell className="max-w-xs whitespace-normal break-words">
                {subject.name}
              </TableCell>
              <TableCell>
                <CatalogStatusBadge active={subject.is_active} />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={t('catalog.actions.editNamed', {
                    name: subject.name,
                  })}
                  onClick={() => onEdit(subject)}
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
