import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
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
import type { FailedJob } from './api';
import { useTaskLabel } from './useTaskLabel';

// The failed jobs (#128 G3, G4, G6): newest failure first; each row shows
// the task label with its code, the status as text, the attempts, the
// error (class, then " · " and the code or "SQLSTATE x"; "Unknown" without
// a class), when it failed (center zone) and the retries. A job's
// arguments are never part of the data. "Retry" only when the job is
// retryable, named with the task and the failed time. "Load more" follows
// the cursor; a failed "Load more" keeps the rows. Below sm the rows are
// cards (nothing scrolls sideways at 320 px). Presentational.

const CARD_ROW =
  'max-sm:flex max-sm:flex-col max-sm:gap-1 max-sm:rounded-lg max-sm:border max-sm:border-border max-sm:p-4';
const CARD_CELL =
  'whitespace-normal max-sm:block max-sm:px-0 max-sm:py-1 max-sm:break-words max-sm:before:block max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]';

export type JobListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly FailedJob[];
      hasMore: boolean;
      loadingMore: boolean;
      /** A failed "Load more" (the rows stay). */
      moreError: unknown;
      onLoadMore: () => void;
    };

interface FailedJobListProps {
  state: JobListState;
  /** The list changed under the cursor (400 invalid_cursor): restart. */
  cursorInvalid: boolean;
  onRestart: () => void;
  onRetryJob: (job: FailedJob) => void;
}

export function FailedJobList({
  state,
  cursorInvalid,
  onRestart,
  onRetryJob,
}: FailedJobListProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const taskLabel = useTaskLabel();
  const id = useId();
  const titleId = `${id}-title`;

  const changedAlert = (
    <ErrorAlert
      error={null}
      message={t('jobs.list.changed')}
      retryLabel={t('jobs.list.reload')}
      onRetry={onRestart}
    />
  );

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    content = cursorInvalid ? (
      changedAlert
    ) : (
      <ErrorAlert error={state.error} onRetry={state.onRetry} />
    );
  } else if (state.items.length === 0) {
    content = <p>{t('jobs.list.empty')}</p>;
  } else {
    content = (
      <>
        <Table aria-labelledby={titleId} className="max-sm:block">
          <TableHeader className="max-sm:sr-only">
            <TableRow>
              <TableHead scope="col">{t('jobs.list.task')}</TableHead>
              <TableHead scope="col">{t('jobs.list.status')}</TableHead>
              <TableHead scope="col">{t('jobs.list.attempts')}</TableHead>
              <TableHead scope="col">{t('jobs.list.error')}</TableHead>
              <TableHead scope="col">{t('jobs.list.failedAt')}</TableHead>
              <TableHead scope="col">{t('jobs.list.retries')}</TableHead>
              <TableHead scope="col">{t('jobs.list.actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="max-sm:flex max-sm:flex-col max-sm:gap-3 max-sm:[&_tr:last-child]:border">
            {state.items.map((job) => {
              const label = taskLabel(job.task_name);
              const failedAt = formatInstant(job.failed_at, 'dateTime');
              return (
                <TableRow key={job.id} className={CARD_ROW}>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('jobs.list.task')}
                  >
                    <span className="flex flex-col">
                      <span className="break-words">{label}</span>
                      <code className="font-mono text-xs break-all text-muted-foreground">
                        {job.task_name}
                      </code>
                    </span>
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('jobs.list.status')}
                  >
                    <span className="inline-flex rounded-full border border-border px-2 py-0.5 text-xs font-medium">
                      {t(`jobs.status.${job.status}`)}
                    </span>
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('jobs.list.attempts')}
                  >
                    {job.attempts}
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('jobs.list.error')}
                  >
                    <ErrorLabel job={job} />
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('jobs.list.failedAt')}
                  >
                    {failedAt}
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('jobs.list.retries')}
                  >
                    {job.retried_count > 0 && job.last_retried_at
                      ? t('jobs.list.retriesSummary', {
                          count: job.retried_count,
                          time: formatInstant(job.last_retried_at, 'dateTime'),
                        })
                      : t('jobs.list.noRetries')}
                  </TableCell>
                  <TableCell className={CARD_CELL}>
                    {job.retryable ? (
                      <Button
                        type="button"
                        variant="outline"
                        className="min-h-11"
                        aria-label={t('jobs.retry.actionFor', {
                          task: label,
                          time: failedAt,
                        })}
                        onClick={() => onRetryJob(job)}
                      >
                        {t('jobs.retry.action')}
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        {state.moreError !== null ? (
          cursorInvalid ? (
            changedAlert
          ) : (
            <ErrorAlert error={state.moreError} onRetry={state.onLoadMore} />
          )
        ) : null}
        {state.hasMore && !cursorInvalid ? (
          <div>
            <Button
              type="button"
              variant="outline"
              className="min-h-11"
              disabled={state.loadingMore}
              onClick={state.onLoadMore}
            >
              {state.loadingMore ? (
                <Spinner label={t('jobs.list.loadingMore')} showLabel />
              ) : (
                t('jobs.list.loadMore')
              )}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section aria-labelledby={titleId} className="flex min-w-0 flex-col gap-4">
      <h2 id={titleId} className="text-lg font-semibold">
        {t('jobs.list.title')}
      </h2>
      {content}
    </section>
  );
}

/** "RuntimeError", "IntegrityError · SQLSTATE 23505", "X · some_code", or
 * "Unknown" (a failure from before failures were recorded). */
export function ErrorLabel({ job }: { job: FailedJob }) {
  const { t } = useTranslation();
  if (job.error_class === null) {
    return <span>{t('jobs.list.unknownError')}</span>;
  }
  const detail = job.error_code
    ? job.error_code
    : job.sqlstate
      ? t('jobs.list.sqlstate', { code: job.sqlstate })
      : null;
  return (
    <code className="font-mono text-xs break-all">
      {detail ? `${job.error_class} · ${detail}` : job.error_class}
    </code>
  );
}
