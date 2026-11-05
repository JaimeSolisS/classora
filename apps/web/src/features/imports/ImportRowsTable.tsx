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
import type { ImportRow, ImportRowPage } from './api';
import { issueText } from './codes';
import { ImportRowStatusBadge } from './ImportBadges';

// The rows of an import's report (M8-05b C2): the file line, the status as
// a badge with its word, the values while the import keeps them, every
// error and warning as a translated message naming its column, and a link
// to the created record. Presentational: the page passes the query state.
// The table scrolls inside its own container on a phone (D1).

export type ImportRowsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ImportRowPage };

interface ImportRowsTableProps {
  state: ImportRowsState;
  /** The import's kind code (the created record's page). */
  kind: string;
  /** The id of the heading that names the table. */
  labelledBy: string;
  onPage: (page: number) => void;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

function recordPath(kind: string, recordId: string): string | null {
  if (kind === 'students') {
    return `/students/${recordId}`;
  }
  if (kind === 'tutors') {
    return `/tutors/${recordId}`;
  }
  return null;
}

function RowValues({ row }: { row: ImportRow }) {
  const entries = Object.entries(row.values ?? {}).filter(
    ([, value]) => value !== '',
  );
  if (entries.length === 0) {
    return <span aria-hidden="true">—</span>;
  }
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-xs">
      {entries.map(([column, value]) => (
        <div key={column} className="contents">
          <dt className="font-mono text-muted-foreground">{column}</dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function RowIssues({ row }: { row: ImportRow }) {
  const { t } = useTranslation();
  if (row.errors.length === 0 && row.warnings.length === 0) {
    return null;
  }
  return (
    <ul className="flex flex-col gap-1">
      {row.errors.map((issue, index) => (
        <li key={`e${index}`}>
          <span className="font-medium text-destructive">
            {t('imports.rows.errorLabel')}
          </span>{' '}
          {issueText(issue, t)}
        </li>
      ))}
      {row.warnings.map((issue, index) => (
        <li key={`w${index}`}>
          <span className="font-medium">{t('imports.rows.warningLabel')}</span>{' '}
          {issueText(issue, t)}
        </li>
      ))}
    </ul>
  );
}

export function ImportRowsTable({
  state,
  kind,
  labelledBy,
  onPage,
}: ImportRowsTableProps) {
  const { t } = useTranslation();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('imports.rows.empty')}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('imports.rows.line')}</TableHead>
            <TableHead scope="col">{t('imports.rows.status')}</TableHead>
            <TableHead scope="col">{t('imports.rows.values')}</TableHead>
            <TableHead scope="col">{t('imports.rows.messages')}</TableHead>
            <TableHead scope="col">{t('imports.rows.record')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((row) => {
            const path = row.record_id ? recordPath(kind, row.record_id) : null;
            return (
              <TableRow key={row.row_number} className="align-top">
                <TableCell>{row.row_number}</TableCell>
                <TableCell>
                  <ImportRowStatusBadge status={row.status} />
                </TableCell>
                <TableCell className="min-w-48 whitespace-normal">
                  <RowValues row={row} />
                </TableCell>
                <TableCell className="min-w-48 whitespace-normal">
                  <RowIssues row={row} />
                </TableCell>
                <TableCell>
                  {path ? (
                    <Link to={path} className={LINK_CLASS}>
                      {t(
                        kind === 'tutors'
                          ? 'imports.rows.openTutor'
                          : 'imports.rows.openStudent',
                      )}
                      <span className="sr-only">
                        {' '}
                        {t('imports.rows.lineNumber', {
                          line: row.row_number,
                        })}
                      </span>
                    </Link>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <Pagination
        offset={offset}
        limit={limit}
        shown={items.length}
        total={total}
        onPage={onPage}
        label={t('imports.rows.pagination')}
        buttonClassName="min-h-11 md:min-h-8"
      />
    </div>
  );
}
