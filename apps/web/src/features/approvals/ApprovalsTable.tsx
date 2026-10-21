import { useId } from 'react';
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
import type {
  ApprovalAction,
  ApprovalRequestItem,
  ApprovalRequestPage,
} from './api';
import { approvalSummary, itemName, typeLabel } from './summary';
import { useSummaryContext } from './useSummaryContext';

// The approval requests (#40 F3): type, a per-type summary, requester,
// submitted at, priority and status (as text), and a button per allowed
// action. Previous/next pages and the empty, loading and error states.
// Presentational: the page passes the query state and the actions.

export type ApprovalsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ApprovalRequestPage };

interface ApprovalsTableProps {
  state: ApprovalsState;
  /** Opens the decision dialog for a row. */
  onDecide: (item: ApprovalRequestItem, action: ApprovalAction) => void;
  onPage: (offset: number) => void;
}

const ACTION_KEYS = {
  approve: {
    label: 'approvals.actions.approve',
    named: 'approvals.actions.approveFor',
  },
  reject: {
    label: 'approvals.actions.reject',
    named: 'approvals.actions.rejectFor',
  },
  request_changes: {
    label: 'approvals.actions.requestChanges',
    named: 'approvals.actions.requestChangesFor',
  },
} as const;

export function ApprovalsTable({
  state,
  onDecide,
  onPage,
}: ApprovalsTableProps) {
  const { t } = useTranslation();
  const context = useSummaryContext();
  const { formatInstant } = useFormatters();
  const id = useId();
  const titleId = `${id}-title`;

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0) {
    content = <p>{t('approvals.list.empty')}</p>;
  } else {
    const { items, total, limit, offset } = state.page;
    const actionable = items.some((item) => item.allowed_actions.length > 0);
    content = (
      <>
        <Table aria-labelledby={titleId}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t('approvals.list.type')}</TableHead>
              <TableHead scope="col">{t('approvals.list.request')}</TableHead>
              <TableHead scope="col">{t('approvals.list.requester')}</TableHead>
              <TableHead scope="col">
                {t('approvals.list.submittedAt')}
              </TableHead>
              <TableHead scope="col">{t('approvals.list.priority')}</TableHead>
              <TableHead scope="col">{t('approvals.list.status')}</TableHead>
              {actionable ? (
                <TableHead scope="col">{t('approvals.list.actions')}</TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>{typeLabel(item.request_type, t)}</TableCell>
                <TableCell className="whitespace-normal">
                  {approvalSummary(item, t, context)}
                </TableCell>
                <TableCell>
                  {item.requester?.display_name ?? t('approvals.list.system')}
                </TableCell>
                <TableCell>
                  {formatInstant(item.created_at, 'dateTime')}
                </TableCell>
                <TableCell>
                  <Badge>{t(`approvals.priority.${item.priority}`)}</Badge>
                </TableCell>
                <TableCell>
                  <Badge>{t(`approvals.status.${item.status}`)}</Badge>
                </TableCell>
                {actionable ? (
                  <TableCell>
                    <ActionButtons item={item} onDecide={onDecide} />
                  </TableCell>
                ) : null}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <nav
          aria-label={t('approvals.list.pagination')}
          className="flex flex-wrap items-center gap-3"
        >
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => onPage(Math.max(0, offset - limit))}
          >
            {t('approvals.list.previous')}
          </Button>
          <p className="text-sm">
            {t('approvals.list.range', {
              from: offset + 1,
              to: offset + items.length,
              total,
            })}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={offset + items.length >= total}
            onClick={() => onPage(offset + limit)}
          >
            {t('approvals.list.next')}
          </Button>
        </nav>
      </>
    );
  }

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-4">
      <h2 id={titleId} className="text-lg font-semibold">
        {t('approvals.list.title')}
      </h2>
      {content}
    </section>
  );
}

/** A text badge (the status and priority are words, never colour alone). */
function Badge({ children }: { children: string }) {
  return (
    <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
      {children}
    </span>
  );
}

function ActionButtons({
  item,
  onDecide,
}: {
  item: ApprovalRequestItem;
  onDecide: (item: ApprovalRequestItem, action: ApprovalAction) => void;
}) {
  const { t } = useTranslation();
  const context = useSummaryContext();
  const name = itemName(item, t, context);
  return (
    <div className="flex flex-wrap gap-2">
      {item.allowed_actions.map((action) => (
        <Button
          key={action}
          type="button"
          size="sm"
          variant={action === 'approve' ? 'default' : 'outline'}
          aria-label={t(ACTION_KEYS[action].named, { name })}
          onClick={() => onDecide(item, action)}
        >
          {t(ACTION_KEYS[action].label)}
        </Button>
      ))}
    </div>
  );
}
