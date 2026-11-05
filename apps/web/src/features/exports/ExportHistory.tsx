import { useTranslation } from 'react-i18next';
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
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { isExportDataset } from './access';
import { exportDownloadUrl, type ExportJob, type ExportJobPage } from './api';
import { availability, formatBytes, timeLeft } from './rules';

// The caller's exports (M8-07b C1), newest first: dataset, format, when it
// was requested, status, rows, size, expiry and the file. A ready export
// within its 24 hours has a plain same-origin Download link (E2, the
// payslip pattern); past its expiry it reads "Expired" with no link; a
// failed one shows why. Presentational: the page passes the query state
// and the time. The table scrolls inside its own container on a phone.

export type ExportHistoryState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ExportJobPage };

interface ExportHistoryProps {
  state: ExportHistoryState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  onPage: (page: number) => void;
  /** The time the expiries are measured against (stories pin it). */
  now?: Date;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

export function ExportHistory({
  state,
  labelledBy,
  onPage,
  now = new Date(),
}: ExportHistoryProps) {
  const { t, i18n } = useTranslation();
  const { formatInstant } = useFormatters();
  const language = toLanguage(i18n.resolvedLanguage);
  const numbers = new Intl.NumberFormat(language);

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('exports.history.empty')}</p>;
  }

  const datasetName = (job: ExportJob) =>
    isExportDataset(job.dataset)
      ? t(`exports.datasets.${job.dataset}`)
      : job.dataset;

  function fileCell(job: ExportJob) {
    const state = availability(job, now);
    switch (state.kind) {
      case 'pending':
        return (
          <span className="text-muted-foreground">
            {t('exports.history.pending')}
          </span>
        );
      case 'expired':
        return <span>{t('exports.history.expired')}</span>;
      case 'failed':
        return (
          <span className="text-destructive" data-export-failure>
            {t(`exports.history.failure.${state.code ?? 'error'}`)}
          </span>
        );
      case 'download': {
        const left = timeLeft(state.expiresAt, now);
        return (
          <span className="flex flex-col items-start">
            <a
              href={exportDownloadUrl(job.id)}
              download={job.file_name}
              className={LINK_CLASS}
              aria-label={t('exports.history.downloadFor', {
                file: job.file_name,
              })}
            >
              {t('exports.history.download')}
            </a>
            <span className="text-sm text-muted-foreground">
              {'hours' in left
                ? t('exports.history.expiresInHours', { hours: left.hours })
                : t('exports.history.expiresInMinutes', {
                    minutes: left.minutes,
                  })}
            </span>
          </span>
        );
      }
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('exports.history.dataset')}</TableHead>
            <TableHead scope="col">{t('exports.history.format')}</TableHead>
            <TableHead scope="col">
              {t('exports.history.requestedAt')}
            </TableHead>
            <TableHead scope="col">{t('exports.history.status')}</TableHead>
            <TableHead scope="col">{t('exports.history.rows')}</TableHead>
            <TableHead scope="col">{t('exports.history.size')}</TableHead>
            <TableHead scope="col">{t('exports.history.expiresAt')}</TableHead>
            <TableHead scope="col">{t('exports.history.file')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((job) => (
            <TableRow
              key={job.id}
              data-export-id={job.id}
              data-export-status={job.status}
            >
              <TableCell>{datasetName(job)}</TableCell>
              <TableCell>{t(`exports.formats.${job.format}`)}</TableCell>
              <TableCell className="whitespace-nowrap">
                {formatInstant(job.created_at, 'dateTime')}
              </TableCell>
              <TableCell>{t(`exports.status.${job.status}`)}</TableCell>
              <TableCell className="tabular-nums">
                {job.row_count === null ? '—' : numbers.format(job.row_count)}
              </TableCell>
              <TableCell className="whitespace-nowrap tabular-nums">
                {job.byte_size === null
                  ? '—'
                  : formatBytes(job.byte_size, language)}
              </TableCell>
              <TableCell className="whitespace-nowrap">
                {job.expires_at === null
                  ? '—'
                  : formatInstant(job.expires_at, 'dateTime')}
              </TableCell>
              <TableCell>{fileCell(job)}</TableCell>
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
        label={t('exports.history.pagination')}
        buttonClassName="min-h-11 md:min-h-8"
      />
    </div>
  );
}
