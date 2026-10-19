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
import type { EducationLevel, EducationLevelPage } from './api';
import { CatalogStatusBadge } from './CatalogStatusBadge';
import { Pagination } from './Pagination';

// The education levels table (#32 H2): name (a link to the level page),
// position, active subjects, status and an Edit button per row, then the
// pagination. Loading, empty and error states. Presentational: the page
// passes the query state. Below `md` the table scrolls inside its own
// container (SC 1.4.10 data-table exception).

export type LevelsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: EducationLevelPage };

interface LevelsTableProps {
  state: LevelsTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  onPage: (page: number) => void;
  onEdit: (level: EducationLevel) => void;
}

export const CATALOG_LINK_CLASS =
  'inline-block min-h-6 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function LevelsTable({
  state,
  labelledBy,
  onPage,
  onEdit,
}: LevelsTableProps) {
  const { t } = useTranslation();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('catalog.levels.empty')}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('catalog.levels.name')}</TableHead>
            <TableHead scope="col">{t('catalog.levels.position')}</TableHead>
            <TableHead scope="col">
              {t('catalog.levels.activeSubjects')}
            </TableHead>
            <TableHead scope="col">{t('catalog.levels.status')}</TableHead>
            <TableHead scope="col">
              <span className="sr-only">{t('catalog.levels.actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((level) => (
            <TableRow key={level.id}>
              <TableCell className="max-w-xs whitespace-normal break-words">
                <Link
                  to={`/catalog/education-levels/${level.id}`}
                  className={CATALOG_LINK_CLASS}
                >
                  {level.name}
                </Link>
              </TableCell>
              <TableCell>{level.sort_order}</TableCell>
              <TableCell>{level.active_subject_count}</TableCell>
              <TableCell>
                <CatalogStatusBadge active={level.is_active} />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={t('catalog.actions.editNamed', {
                    name: level.name,
                  })}
                  onClick={() => onEdit(level)}
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
