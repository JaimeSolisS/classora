import { Fragment, useId, useState } from 'react';
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
import { isAutomaticAction, shortId, type AuditEvent } from './api';
import { AuditChanges } from './AuditChanges';

// The audit events (#120 C3, C4, C6): newest first; each row shows the date
// and time with seconds (center zone), the person ("System" without an
// actor, "Unknown user" with the short id when the id has no name), the
// action (translated for the five automatic ones, else the code) and the
// record (type code and short id, or "—"). Row actions: "View changes" (a
// disclosure), "View this record's history" and "View this person's
// actions" (each named with the row's time, so unique per row). "Load more"
// follows the cursor; a failed "Load more" keeps the rows. Below sm the
// rows are cards (C7: nothing scrolls sideways at 320 px). Presentational.

const CARD_ROW =
  'max-sm:flex max-sm:flex-col max-sm:gap-1 max-sm:rounded-lg max-sm:border max-sm:border-border max-sm:p-4';
const CARD_CELL =
  'whitespace-normal max-sm:block max-sm:px-0 max-sm:py-1 max-sm:break-words max-sm:before:block max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]';
const ACTION_BUTTON = 'min-h-11 whitespace-normal text-left';

export type AuditListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly AuditEvent[];
      hasMore: boolean;
      loadingMore: boolean;
      /** A failed "Load more" (the rows stay). */
      moreError: unknown;
      onLoadMore: () => void;
    };

interface AuditEventListProps {
  state: AuditListState;
  /** The list changed under the cursor (400 invalid_cursor): restart. */
  cursorInvalid: boolean;
  onRestart: () => void;
  onRecord: (event: AuditEvent) => void;
  onActor: (event: AuditEvent) => void;
}

export function AuditEventList({
  state,
  cursorInvalid,
  onRestart,
  onRecord,
  onActor,
}: AuditEventListProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const titleId = `${id}-title`;
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  function toggle(eventId: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(eventId)) {
        next.delete(eventId);
      } else {
        next.add(eventId);
      }
      return next;
    });
  }

  const changedAlert = (
    <ErrorAlert
      error={null}
      message={t('audit.list.changed')}
      retryLabel={t('audit.list.reload')}
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
    content = <p>{t('audit.list.empty')}</p>;
  } else {
    content = (
      <>
        <Table aria-labelledby={titleId} className="max-sm:block">
          <TableHeader className="max-sm:sr-only">
            <TableRow>
              <TableHead scope="col">{t('audit.list.occurredAt')}</TableHead>
              <TableHead scope="col">{t('audit.list.person')}</TableHead>
              <TableHead scope="col">{t('audit.list.action')}</TableHead>
              <TableHead scope="col">{t('audit.list.record')}</TableHead>
              <TableHead scope="col">{t('audit.list.actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="max-sm:flex max-sm:flex-col max-sm:gap-3 max-sm:[&_tr:last-child]:border">
            {state.items.map((event) => {
              const time = formatInstant(event.occurred_at, 'dateTimeSeconds');
              const expanded = open.has(event.id);
              const regionId = `${id}-changes-${event.id}`;
              return (
                <Fragment key={event.id}>
                  <TableRow className={CARD_ROW}>
                    <TableCell
                      className={CARD_CELL}
                      data-label={t('audit.list.occurredAt')}
                    >
                      {time}
                    </TableCell>
                    <TableCell
                      className={CARD_CELL}
                      data-label={t('audit.list.person')}
                    >
                      <Person event={event} />
                    </TableCell>
                    <TableCell
                      className={CARD_CELL}
                      data-label={t('audit.list.action')}
                    >
                      {isAutomaticAction(event.action) ? (
                        t(`audit.actions.${event.action}`)
                      ) : (
                        <code className="font-mono text-xs break-all">
                          {event.action}
                        </code>
                      )}
                    </TableCell>
                    <TableCell
                      className={CARD_CELL}
                      data-label={t('audit.list.record')}
                    >
                      {event.record_type && event.record_id ? (
                        <span className="flex flex-col">
                          <code className="font-mono text-xs break-all">
                            {event.record_type}
                          </code>
                          <span
                            className="font-mono text-xs text-muted-foreground"
                            title={event.record_id}
                          >
                            {shortId(event.record_id)}
                          </span>
                        </span>
                      ) : (
                        t('audit.diff.none')
                      )}
                    </TableCell>
                    <TableCell className={CARD_CELL}>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          className={ACTION_BUTTON}
                          aria-expanded={expanded}
                          aria-controls={regionId}
                          aria-label={t('audit.row.viewChangesFor', { time })}
                          onClick={() => toggle(event.id)}
                        >
                          {t('audit.row.viewChanges')}
                        </Button>
                        {event.record_type && event.record_id ? (
                          <Button
                            type="button"
                            variant="outline"
                            className={ACTION_BUTTON}
                            aria-label={t('audit.row.recordHistoryFor', {
                              time,
                            })}
                            onClick={() => onRecord(event)}
                          >
                            {t('audit.row.recordHistory')}
                          </Button>
                        ) : null}
                        {event.actor_id ? (
                          <Button
                            type="button"
                            variant="outline"
                            className={ACTION_BUTTON}
                            aria-label={t('audit.row.personActionsFor', {
                              time,
                            })}
                            onClick={() => onActor(event)}
                          >
                            {t('audit.row.personActions')}
                          </Button>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                  {expanded ? (
                    <TableRow className={CARD_ROW}>
                      <TableCell colSpan={5} className={CARD_CELL}>
                        <AuditChanges event={event} id={regionId} />
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
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
                <Spinner label={t('audit.list.loadingMore')} showLabel />
              ) : (
                t('audit.list.loadMore')
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
        {t('audit.list.title')}
      </h2>
      {content}
    </section>
  );
}

function Person({ event }: { event: AuditEvent }) {
  const { t } = useTranslation();
  if (event.actor) {
    return <span className="break-words">{event.actor.display_name}</span>;
  }
  if (event.actor_id === null) {
    return <span>{t('audit.list.system')}</span>;
  }
  return (
    <span>{t('audit.list.unknownUser', { id: shortId(event.actor_id) })}</span>
  );
}
