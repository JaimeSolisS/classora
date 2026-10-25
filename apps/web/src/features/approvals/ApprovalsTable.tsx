import { useId } from 'react';
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
import { useFormatters } from '@/lib/formatters';
import type {
  ApprovalAction,
  ApprovalRequestItem,
  ApprovalRequestPage,
} from './api';
import {
  actionLabelKey,
  approvalSummary,
  availabilityChangeLink,
  itemName,
  namedActionLabelKey,
  reassignmentLink,
  sessionCloseoutLink,
  sessionCloseoutReviewLine,
  reassignmentSubject,
  itemReviewLink,
  typeLabel,
} from './summary';

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

export function ApprovalsTable({
  state,
  onDecide,
  onPage,
}: ApprovalsTableProps) {
  const { t } = useTranslation();
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
    const actionable = items.some(
      (item) =>
        item.allowed_actions.length > 0 || itemReviewLink(item) !== null,
    );
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
                  <RequestSummary item={item} />
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

/** The summary, with the session link of a reassignment item (#52 H3). */
function RequestSummary({ item }: { item: ApprovalRequestItem }) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const summary = approvalSummary(
    item,
    t,
    (instant) => formatInstant(instant, 'dateTime'),
    (localDate) => formatLocalDate(localDate),
  );
  const availability = availabilityChangeLink(item);
  if (availability) {
    // #63 H2: the tutor's availability.
    return (
      <span className="flex flex-col gap-1">
        <span>{summary}</span>
        <Link
          to={availability}
          className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('approvals.availabilityChange.openAvailability')}
        </Link>
      </span>
    );
  }
  const closeout = sessionCloseoutLink(item);
  if (closeout) {
    // #77 G4, #78 G2: what the item asks and a "Review" link to the
    // session page's Closeout section; the decisions are made there.
    const line = sessionCloseoutReviewLine(item, t);
    return (
      <span className="flex flex-col gap-1">
        {line ? <span className="text-sm font-medium">{line}</span> : null}
        <span>{summary}</span>
        <Link
          to={closeout}
          className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('approvals.closeout.review')}
        </Link>
      </span>
    );
  }
  const link = reassignmentLink(item);
  if (!link) {
    return <>{summary}</>;
  }
  const note = reassignmentSubject(item)?.decline_note;
  return (
    <span className="flex flex-col gap-1">
      <span>{summary}</span>
      {note ? (
        <span className="text-sm break-words text-muted-foreground">
          {t('approvals.reassignment.note', { note })}
        </span>
      ) : null}
      <Link
        to={link}
        className="text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        {t('approvals.reassignment.openSession')}
      </Link>
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
  const { formatInstant, formatLocalDate } = useFormatters();
  const name = itemName(
    item,
    t,
    (instant) => formatInstant(instant, 'dateTime'),
    (localDate) => formatLocalDate(localDate),
  );
  const review = itemReviewLink(item);
  if (review) {
    // #60 H2, #62 G2: session and change requests are decided on their
    // review pages.
    return (
      <Link
        to={review}
        aria-label={t('approvals.sessionRequest.reviewFor', { name })}
        className="inline-flex h-8 items-center rounded-md border border-border px-3 text-sm font-medium underline-offset-4 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring"
      >
        {t('approvals.sessionRequest.review')}
      </Link>
    );
  }
  return (
    <div className="flex flex-wrap gap-2">
      {item.allowed_actions.map((action) => (
        <Button
          key={action}
          type="button"
          size="sm"
          variant={action === 'approve' ? 'default' : 'outline'}
          aria-label={t(namedActionLabelKey(item.request_type, action), {
            name,
          })}
          onClick={() => onDecide(item, action)}
        >
          {t(actionLabelKey(item.request_type, action))}
        </Button>
      ))}
    </div>
  );
}
