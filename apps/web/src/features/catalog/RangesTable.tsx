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
import { useFormatters } from '@/lib/formatters';
import { CatalogStatusBadge } from './CatalogStatusBadge';
import { Pagination } from './Pagination';

// The academic periods and closures tables (#38 E2, E3): name, type, the
// dates (calendar dates, formatted without any zone conversion), status
// and an Edit button per row, then the pagination. Periods show start and
// end in two columns; closures show one "Dates" column with a single date
// when the closure lasts one day. Loading, empty and error states.
// Presentational: the page passes the query state. Below `md` the table
// scrolls inside its own container (SC 1.4.10 data-table exception).

export interface RangeRow {
  id: string;
  name: string;
  /** The translated type. */
  typeLabel: string;
  starts_on: string;
  ends_on: string;
  is_active: boolean;
}

export interface RangePage<T extends RangeRow> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type RangesTableState<T extends RangeRow> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: RangePage<T> };

interface RangesTableProps<T extends RangeRow> {
  state: RangesTableState<T>;
  /** `split`: start and end columns; `combined`: one "Dates" column. */
  dates: 'split' | 'combined';
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** The empty state's text. */
  emptyText: string;
  onPage: (page: number) => void;
  onEdit: (row: T) => void;
}

export function RangesTable<T extends RangeRow>({
  state,
  dates,
  labelledBy,
  emptyText,
  onPage,
  onEdit,
}: RangesTableProps<T>) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{emptyText}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('catalog.range.name')}</TableHead>
            <TableHead scope="col">{t('catalog.range.type')}</TableHead>
            {dates === 'split' ? (
              <>
                <TableHead scope="col">{t('catalog.range.startsOn')}</TableHead>
                <TableHead scope="col">{t('catalog.range.endsOn')}</TableHead>
              </>
            ) : (
              <TableHead scope="col">{t('catalog.range.dates')}</TableHead>
            )}
            <TableHead scope="col">{t('catalog.range.status')}</TableHead>
            <TableHead scope="col">
              <span className="sr-only">{t('catalog.range.actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((row) => (
            <TableRow key={row.id}>
              <TableCell className="max-w-xs whitespace-normal break-words">
                {row.name}
              </TableCell>
              <TableCell>{row.typeLabel}</TableCell>
              {dates === 'split' ? (
                <>
                  <TableCell>{formatLocalDate(row.starts_on)}</TableCell>
                  <TableCell>{formatLocalDate(row.ends_on)}</TableCell>
                </>
              ) : (
                <TableCell>
                  {row.starts_on === row.ends_on
                    ? formatLocalDate(row.starts_on)
                    : t('catalog.range.dateRange', {
                        start: formatLocalDate(row.starts_on),
                        end: formatLocalDate(row.ends_on),
                      })}
                </TableCell>
              )}
              <TableCell>
                <CatalogStatusBadge active={row.is_active} />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={t('catalog.actions.editNamed', {
                    name: row.name,
                  })}
                  onClick={() => onEdit(row)}
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
