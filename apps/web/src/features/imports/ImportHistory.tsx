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
import { useFormatters } from '@/lib/formatters';
import type { ImportJobPage } from './api';
import { ImportStatusBadge } from './ImportBadges';
import { isImportKind } from './kinds';
import { showsOutcome } from './rules';

// The import history (M8-05b B4): kind, file, status, counts, who and when,
// newest first, each row a link to its report. Loading, empty and error
// states. Presentational: the page passes the query state. The table
// scrolls inside its own container on a phone (the Table component).

export type ImportHistoryState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ImportJobPage };

interface ImportHistoryProps {
  state: ImportHistoryState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Whether a filter is applied (changes the empty message). */
  filtered: boolean;
  onPage: (page: number) => void;
}

export function ImportHistory({
  state,
  labelledBy,
  filtered,
  onPage,
}: ImportHistoryProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return (
      <p>
        {filtered ? t('imports.history.noResults') : t('imports.history.empty')}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('imports.history.file')}</TableHead>
            <TableHead scope="col">{t('imports.history.kind')}</TableHead>
            <TableHead scope="col">{t('imports.history.status')}</TableHead>
            <TableHead scope="col">{t('imports.history.counts')}</TableHead>
            <TableHead scope="col">{t('imports.history.createdBy')}</TableHead>
            <TableHead scope="col">{t('imports.history.createdAt')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((job) => (
            <TableRow key={job.id}>
              <TableCell>
                <Link
                  to={`/imports/${job.id}`}
                  className="inline-flex min-h-11 max-w-64 items-center font-medium [overflow-wrap:anywhere] underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6"
                >
                  {job.file_name ?? t('imports.history.noFileName')}
                </Link>
              </TableCell>
              <TableCell>
                {isImportKind(job.kind)
                  ? t(`imports.kinds.${job.kind}`)
                  : job.kind}
              </TableCell>
              <TableCell>
                <ImportStatusBadge status={job.status} />
              </TableCell>
              <TableCell>
                {showsOutcome(job)
                  ? t('imports.history.outcomeCounts', {
                      total: job.counts.total,
                      created: job.counts.created,
                      skipped: job.counts.skipped,
                      failed: job.counts.failed,
                    })
                  : t('imports.history.reportCounts', {
                      total: job.counts.total,
                      ready: job.counts.ready,
                      duplicate: job.counts.duplicate,
                      error: job.counts.error,
                    })}
              </TableCell>
              <TableCell>{job.created_by.display_name}</TableCell>
              <TableCell className="whitespace-nowrap">
                {formatInstant(job.created_at, 'dateTime')}
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
        label={t('imports.history.pagination')}
        buttonClassName="min-h-11 md:min-h-8"
      />
    </div>
  );
}
