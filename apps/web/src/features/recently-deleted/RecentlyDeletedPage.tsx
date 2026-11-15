import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { useDocumentTitle } from '@/app/useDocumentTitle';
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
import {
  DELETED_RECORDS_KEY,
  fetchDeletedRecords,
  recordPath,
  restoreDeletedRecord,
  type DeletedRecord,
  type DeletedRecordList,
} from './api';

// /recently-deleted (#129 D): the records the user deleted in the last 30
// days and may still restore, newest first (at most 100; a note says so
// when there are more). Each row names the kind, the record (its title, or
// the kind and its date), the student, when it was deleted and until when
// it can be restored (center-local dates). "Restore" needs no confirmation
// (it is safe): a 200 removes the row and says so in the polite status,
// with an "Open" link to the record's page (the app has no toast, as #128);
// a 404 or a restore conflict shows its own message and refreshes the
// list; a 403 shows the kind's own message. Below sm the rows are cards
// (nothing scrolls sideways at 320 px).

const CARD_ROW =
  'max-sm:flex max-sm:flex-col max-sm:gap-1 max-sm:rounded-lg max-sm:border max-sm:border-border max-sm:p-4';
const CARD_CELL =
  'whitespace-normal max-sm:block max-sm:px-0 max-sm:py-1 max-sm:break-words max-sm:before:block max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]';

interface Restored {
  label: string;
  path: string | null;
}

function useRecordLabel() {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  return (record: DeletedRecord) =>
    record.title ??
    t('recentlyDeleted.untitled', {
      kind: t(`recentlyDeleted.kinds.${record.kind}`),
      date: formatLocalDate(record.reference_date),
    });
}

export function RecentlyDeletedPage() {
  const { t } = useTranslation();
  const { t: errorsT } = useTranslation('errors');
  useDocumentTitle(t('recentlyDeleted.title'));
  const queryClient = useQueryClient();
  const recordLabel = useRecordLabel();
  const [restored, setRestored] = useState<Restored | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  const list = useQuery({
    queryKey: DELETED_RECORDS_KEY,
    queryFn: ({ signal }) => fetchDeletedRecords(signal),
    retry: false,
  });

  async function restore(record: DeletedRecord) {
    setBusy(record.id);
    setRefusal(null);
    try {
      await restoreDeletedRecord(record);
      queryClient.setQueryData<DeletedRecordList>(
        DELETED_RECORDS_KEY,
        (data) =>
          data
            ? {
                ...data,
                items: data.items.filter((item) => item.id !== record.id),
              }
            : data,
      );
      setRestored({
        label: recordLabel(record),
        path: recordPath(record.kind, record.id),
      });
      // The row (and its focused button) is gone: back to the page heading.
      heading.current?.focus();
    } catch (error) {
      setRestored(null);
      const notFound = error instanceof ApiError && error.status === 404;
      const conflict =
        error instanceof ApiError &&
        error.code === 'deleted_record_restore_conflict';
      if (notFound || conflict) {
        setRefusal(
          t(
            notFound
              ? 'recentlyDeleted.restore.notFound'
              : 'recentlyDeleted.restore.conflict',
          ),
        );
        void queryClient.invalidateQueries({ queryKey: DELETED_RECORDS_KEY });
      } else {
        // The kind's own 403 (for example note_read_only), a version
        // conflict, or any other error: its catalog message.
        setRefusal(errorMessage(error, errorsT));
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {t('recentlyDeleted.title')}
        </h1>
        <p>{t('recentlyDeleted.intro')}</p>
      </div>
      <p aria-live="polite" aria-atomic="true" className="text-sm">
        {restored ? (
          <>
            {t('recentlyDeleted.restore.done', { record: restored.label })}{' '}
            {restored.path ? (
              <Link
                to={restored.path}
                className="inline-flex min-h-11 items-center font-medium underline underline-offset-4"
                aria-label={t('recentlyDeleted.restore.openFor', {
                  record: restored.label,
                })}
              >
                {t('recentlyDeleted.restore.open')}
              </Link>
            ) : null}
          </>
        ) : null}
      </p>
      {refusal ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {refusal}
        </p>
      ) : null}
      <DeletedRecordTable
        list={list.data}
        loading={list.isPending}
        error={list.isError ? list.error : null}
        busy={busy}
        onRetry={() => void list.refetch()}
        onRestore={(record) => void restore(record)}
      />
    </div>
  );
}

interface DeletedRecordTableProps {
  list: DeletedRecordList | undefined;
  loading: boolean;
  error: unknown;
  busy: string | null;
  onRetry: () => void;
  onRestore: (record: DeletedRecord) => void;
}

export function DeletedRecordTable({
  list,
  loading,
  error,
  busy,
  onRetry,
  onRestore,
}: DeletedRecordTableProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const recordLabel = useRecordLabel();
  const titleId = `${useId()}-title`;

  let content;
  if (error !== null) {
    content = <ErrorAlert error={error} onRetry={onRetry} />;
  } else if (loading || !list) {
    content = <LoadingState layout="table" rows={4} />;
  } else if (list.items.length === 0) {
    content = <p>{t('recentlyDeleted.empty')}</p>;
  } else {
    content = (
      <>
        {list.has_more ? (
          <p className="text-sm">{t('recentlyDeleted.onlyRecent')}</p>
        ) : null}
        <Table aria-labelledby={titleId} className="max-sm:block">
          <TableHeader className="max-sm:sr-only">
            <TableRow>
              <TableHead scope="col">
                {t('recentlyDeleted.columns.kind')}
              </TableHead>
              <TableHead scope="col">
                {t('recentlyDeleted.columns.record')}
              </TableHead>
              <TableHead scope="col">
                {t('recentlyDeleted.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('recentlyDeleted.columns.deleted')}
              </TableHead>
              <TableHead scope="col">
                {t('recentlyDeleted.columns.available')}
              </TableHead>
              <TableHead scope="col">
                {t('recentlyDeleted.columns.actions')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="max-sm:flex max-sm:flex-col max-sm:gap-3 max-sm:[&_tr:last-child]:border">
            {list.items.map((record) => {
              const kind = t(`recentlyDeleted.kinds.${record.kind}`);
              const label = recordLabel(record);
              const restoring = busy === record.id;
              return (
                <TableRow key={record.id} className={CARD_ROW}>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('recentlyDeleted.columns.kind')}
                  >
                    {kind}
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('recentlyDeleted.columns.record')}
                  >
                    <span className="font-medium break-words">{label}</span>
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('recentlyDeleted.columns.student')}
                  >
                    <span className="break-words">
                      {record.student.display_name}
                    </span>
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('recentlyDeleted.columns.deleted')}
                  >
                    {t('recentlyDeleted.deletedOn', {
                      date: formatInstant(record.deleted_at, 'date'),
                    })}
                  </TableCell>
                  <TableCell
                    className={CARD_CELL}
                    data-label={t('recentlyDeleted.columns.available')}
                  >
                    {t('recentlyDeleted.availableUntil', {
                      date: formatInstant(record.restorable_until, 'date'),
                    })}
                  </TableCell>
                  <TableCell className={CARD_CELL}>
                    <Button
                      type="button"
                      variant="outline"
                      className="min-h-11"
                      disabled={busy !== null}
                      aria-label={t('recentlyDeleted.restore.actionFor', {
                        kind,
                        record: label,
                      })}
                      onClick={() => onRestore(record)}
                    >
                      {restoring ? (
                        <Spinner
                          label={t('recentlyDeleted.restore.busy')}
                          showLabel
                        />
                      ) : (
                        t('recentlyDeleted.restore.action')
                      )}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </>
    );
  }

  return (
    <section aria-labelledby={titleId} className="flex min-w-0 flex-col gap-4">
      <h2 id={titleId} className="text-lg font-semibold">
        {t('recentlyDeleted.listTitle')}
      </h2>
      {content}
    </section>
  );
}

export default RecentlyDeletedPage;
