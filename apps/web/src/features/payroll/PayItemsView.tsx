import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { useFormatters } from '@/lib/formatters';
import { PAY_ITEMS_ANCHOR, type PayItem } from './api';

// The tutor's bonuses and deductions (#454 B1, B2, B6, B8), without any query:
// a table from `md` up and cards below. A deduction reads as one in words and
// with a minus sign, never by colour alone. Every one-time item says where it
// lands, a waiting one too, so it can't go unnoticed. `actions` adds the
// controls; without it the section is read-only. Applied items offer no
// delete. Nothing computes an amount: each is the API's string, formatted.

export type PayItemsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly PayItem[];
      hasMore: boolean;
      loadingMore: boolean;
      moreError: unknown;
      onMore: () => void;
      /** The last refresh failed: the rows on screen may be out of date. */
      refreshError: unknown;
      onRefresh: () => void;
    };

export interface PayItemActions {
  onAdd: () => void;
  onEnd: (item: PayItem) => void;
  /** Rejects with the error to show in the confirmation. */
  onDelete: (item: PayItem) => Promise<void>;
}

export interface PayItemsViewProps {
  state: PayItemsState;
  /** The controls: payroll.manage, and not a non-Owner's own pay. */
  actions?: PayItemActions;
  /** Moves the keyboard to the section's heading, for the #pay-items link. */
  focusOnMount?: boolean;
  /** Each new value moves the keyboard to the heading: the row that opened a
   * dialog is gone, so its button can't take the focus back. */
  focusRequest?: number;
  announcement?: string;
  /** Stories: the item whose confirmation starts open, and its refusal. */
  initialDelete?: PayItem;
  initialDeleteError?: unknown;
}

const TEXT_CLASS = '[overflow-wrap:anywhere]';

function useItemText() {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();

  /** "Where it lands" (B2): applied beats everything for a one-time item. */
  const lands = (item: PayItem) => {
    const from = formatLocalDate(item.effective_from);
    if (item.recurrence === 'recurring') {
      return item.effective_to
        ? t('payroll.payItems.lands.everyRange', {
            from,
            to: formatLocalDate(item.effective_to),
          })
        : t('payroll.payItems.lands.everyOpen', { from });
    }
    if (item.applied) {
      return t('payroll.payItems.lands.paid');
    }
    const period = item.placed_period;
    return period
      ? t('payroll.payItems.lands.period', {
          from: formatLocalDate(period.starts_on),
          to: formatLocalDate(period.ends_on),
          status: t(`payroll.payItems.lands.status.${period.status}`),
        })
      : t('payroll.payItems.lands.waiting');
  };

  return (item: PayItem) => {
    const from = formatLocalDate(item.effective_from);
    return {
      type: item.pay_item_type.name,
      direction: t(
        `payroll.payItems.directions.${item.pay_item_type.direction}`,
      ),
      amount: formatMoney(
        item.pay_item_type.direction === 'deduction'
          ? `-${item.amount}`
          : item.amount,
      ),
      recurrence: t(`payroll.payItems.recurrences.${item.recurrence}`),
      date: from,
      dates:
        item.recurrence === 'one_time'
          ? from
          : item.effective_to
            ? t('payroll.payItems.rangeDates', {
                from,
                to: formatLocalDate(item.effective_to),
              })
            : t('payroll.payItems.openDates', { from }),
      lands: lands(item),
    };
  };
}

function ItemActions({
  item,
  actions,
  onDelete,
}: {
  item: PayItem;
  actions: PayItemActions;
  onDelete: (item: PayItem) => void;
}) {
  const { t } = useTranslation();
  const text = useItemText()(item);
  const label = { type: text.type, date: text.date };
  const ended = item.effective_to !== null;
  return (
    <div className="flex flex-wrap gap-2">
      {item.recurrence === 'recurring' ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="min-h-11 md:min-h-8"
          aria-label={
            ended
              ? t('payroll.payItems.changeEndFor', label)
              : t('payroll.payItems.endFor', label)
          }
          onClick={() => actions.onEnd(item)}
        >
          {ended ? t('payroll.payItems.changeEnd') : t('payroll.payItems.end')}
        </Button>
      ) : null}
      {item.applied ? null : (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="min-h-11 md:min-h-8"
          aria-label={t('payroll.payItems.deleteFor', label)}
          onClick={() => onDelete(item)}
        >
          {t('payroll.payItems.delete')}
        </Button>
      )}
    </div>
  );
}

function Items({
  items,
  actions,
  onDelete,
}: {
  items: readonly PayItem[];
  actions?: PayItemActions;
  onDelete: (item: PayItem) => void;
}) {
  const { t } = useTranslation();
  const text = useItemText();
  return (
    <>
      <div className="hidden min-w-0 md:block" data-pay-items-table>
        <Table>
          <TableCaption className="sr-only">
            {t('payroll.payItems.caption')}
          </TableCaption>
          <TableHeader>
            <TableRow>
              {(
                [
                  'type',
                  'description',
                  'amount',
                  'recurrence',
                  'dates',
                  'lands',
                ] as const
              ).map((column) => (
                <TableHead key={column} scope="col">
                  {t(`payroll.payItems.columns.${column}`)}
                </TableHead>
              ))}
              {actions ? (
                <TableHead scope="col">
                  {t('payroll.payItems.columns.actions')}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const row = text(item);
              return (
                <TableRow
                  key={item.id}
                  data-pay-item-id={item.id}
                  data-direction={item.pay_item_type.direction}
                  className="align-top"
                >
                  <TableCell className={`whitespace-normal ${TEXT_CLASS}`}>
                    <span className="block">{row.type}</span>
                    <span className="block text-muted-foreground">
                      {row.direction}
                    </span>
                  </TableCell>
                  <TableCell
                    className={`max-w-64 min-w-40 whitespace-normal ${TEXT_CLASS}`}
                  >
                    {item.description}
                  </TableCell>
                  <TableCell className="font-medium" data-pay-item-amount>
                    {row.amount}
                  </TableCell>
                  <TableCell>{row.recurrence}</TableCell>
                  <TableCell className="whitespace-normal">
                    {row.dates}
                  </TableCell>
                  <TableCell className="whitespace-normal" data-pay-item-lands>
                    {row.lands}
                  </TableCell>
                  {actions ? (
                    <TableCell>
                      <ItemActions
                        item={item}
                        actions={actions}
                        onDelete={onDelete}
                      />
                    </TableCell>
                  ) : null}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-pay-items-cards>
        {items.map((item) => {
          const row = text(item);
          return (
            <li
              key={item.id}
              data-pay-item-card={item.id}
              data-direction={item.pay_item_type.direction}
              className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
            >
              <p className={`font-medium ${TEXT_CLASS}`}>
                {row.type} · {row.direction}
              </p>
              <p className="font-medium" data-pay-item-amount>
                {row.amount}
              </p>
              <p className={`text-sm ${TEXT_CLASS}`}>{item.description}</p>
              <dl className="grid grid-cols-1 gap-1 text-sm">
                {(
                  [
                    [t('payroll.payItems.columns.recurrence'), row.recurrence],
                    [t('payroll.payItems.columns.dates'), row.dates],
                    [t('payroll.payItems.columns.lands'), row.lands],
                  ] as const
                ).map(([term, value]) => (
                  <div key={term} className="min-w-0">
                    <dt className="font-medium">{term}</dt>
                    <dd className="break-words">{value}</dd>
                  </div>
                ))}
              </dl>
              {actions ? (
                <ItemActions
                  item={item}
                  actions={actions}
                  onDelete={onDelete}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function PayItemsView({
  state,
  actions,
  focusOnMount = false,
  focusRequest = 0,
  announcement = '',
  initialDelete,
  initialDeleteError = null,
}: PayItemsViewProps) {
  const { t } = useTranslation();
  const itemText = useItemText();
  const heading = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [deleting, setDeleting] = useState<PayItem | null>(
    initialDelete ?? null,
  );
  const [deleteError, setDeleteError] = useState<unknown>(initialDeleteError);

  useEffect(() => {
    if (focusOnMount) {
      headingRef.current?.focus();
    }
  }, [focusOnMount]);

  useEffect(() => {
    if (focusRequest === 0) {
      return;
    }
    // After the dialog's own cleanup.
    const timer = window.setTimeout(() => headingRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [focusRequest]);

  function startDelete(item: PayItem) {
    setDeleteError(null);
    setDeleting(item);
  }

  async function confirmDelete() {
    if (!deleting || !actions) {
      return;
    }
    setDeleteError(null);
    try {
      await actions.onDelete(deleting);
      setDeleting(null);
      // The row that opened the dialog is gone, so its button can't take the
      // focus back (D3). After the dialog's own cleanup.
      window.setTimeout(() => headingRef.current?.focus(), 0);
    } catch (caught) {
      setDeleteError(caught);
    }
  }

  return (
    <section
      id={PAY_ITEMS_ANCHOR}
      aria-labelledby={heading}
      data-pay-items-section
      className="flex min-w-0 scroll-mt-20 flex-col gap-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2
          id={heading}
          ref={headingRef}
          tabIndex={-1}
          className="text-lg font-semibold outline-none"
        >
          {t('payroll.payItems.title')}
        </h2>
        {actions ? (
          <Button
            type="button"
            className="min-h-11 md:min-h-9"
            onClick={actions.onAdd}
          >
            {t('payroll.payItems.add')}
          </Button>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground" data-pay-items-note>
        {t('payroll.payItems.note')}
      </p>
      {state.status === 'loading' ? <LoadingState /> : null}
      {state.status === 'error' ? (
        <ErrorAlert error={state.error} onRetry={state.onRetry} />
      ) : null}
      {state.status === 'ready' && state.refreshError ? (
        <ErrorAlert
          error={state.refreshError}
          message={t('payroll.payItems.stale')}
          onRetry={state.onRefresh}
          retryLabel={t('payroll.payItems.reloadList')}
        />
      ) : null}
      {state.status === 'ready' && state.items.length === 0 ? (
        <p className="text-sm" data-pay-items-empty>
          {t('payroll.payItems.empty')}
        </p>
      ) : null}
      {state.status === 'ready' && state.items.length > 0 ? (
        <Items items={state.items} actions={actions} onDelete={startDelete} />
      ) : null}
      {state.status === 'ready' && state.moreError ? (
        <ErrorAlert error={state.moreError} onRetry={state.onMore} />
      ) : null}
      {state.status === 'ready' && state.hasMore ? (
        <div>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={state.loadingMore}
            onClick={state.onMore}
          >
            {state.loadingMore ? (
              <Spinner label={t('payroll.payItems.more')} showLabel />
            ) : (
              t('payroll.payItems.more')
            )}
          </Button>
        </div>
      ) : null}
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {actions ? (
        <ConfirmDialog
          open={deleting !== null}
          title={t('payroll.payItems.deleteDialog.title')}
          body={
            deleting
              ? t('payroll.payItems.deleteDialog.body', {
                  ...itemText(deleting),
                })
              : ''
          }
          confirmLabel={t('payroll.payItems.deleteDialog.confirm')}
          destructive
          alert={deleteError ? <ErrorAlert error={deleteError} /> : null}
          onConfirm={confirmDelete}
          onCancel={() => {
            setDeleting(null);
            setDeleteError(null);
          }}
        />
      ) : null}
    </section>
  );
}
