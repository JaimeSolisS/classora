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
  progressReportReviewLink,
  typeLabel,
} from './summary';

// The approval requests (#40 F3): type, a per-type summary, requester,
// submitted at, priority, status (as text) and assignee (M8-01b A3), a
// "View details" button (named with the summary, so unique per row) and a
// button per allowed action. Below sm the rows are cards (D3: nothing
// scrolls sideways at 320 px; one DOM, so no control is repeated). Previous/next pages and the empty, loading
// and error states. Presentational: the page passes the query state and the
// actions.

// Below sm each row is a card (D3): the cells stack, each with its column's
// name before it (from `data-label`; the header row stays for screen
// readers only).
const CARD_ROW =
  'max-sm:flex max-sm:flex-col max-sm:gap-1 max-sm:rounded-lg max-sm:border max-sm:border-border max-sm:p-4';
const CARD_CELL =
  'max-sm:block max-sm:px-0 max-sm:py-1 max-sm:whitespace-normal max-sm:break-words max-sm:before:block max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]';

export type ApprovalsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: ApprovalRequestPage };

interface ApprovalsTableProps {
  state: ApprovalsState;
  /** Opens the decision dialog for a row. */
  onDecide: (item: ApprovalRequestItem, action: ApprovalAction) => void;
  /** Opens the detail panel of a row; `trigger` gets focus back. */
  onOpen: (item: ApprovalRequestItem, trigger: HTMLElement) => void;
  onPage: (offset: number) => void;
}

export function ApprovalsTable({
  state,
  onDecide,
  onOpen,
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
    content = (
      <>
        <Table aria-labelledby={titleId} className="max-sm:block">
          <TableHeader className="max-sm:sr-only">
            <TableRow>
              <TableHead scope="col">{t('approvals.list.type')}</TableHead>
              <TableHead scope="col">{t('approvals.list.request')}</TableHead>
              <TableHead scope="col">{t('approvals.list.requester')}</TableHead>
              <TableHead scope="col">
                {t('approvals.list.submittedAt')}
              </TableHead>
              <TableHead scope="col">{t('approvals.list.priority')}</TableHead>
              <TableHead scope="col">{t('approvals.list.status')}</TableHead>
              <TableHead scope="col">{t('approvals.list.assignee')}</TableHead>
              <TableHead scope="col">{t('approvals.list.actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="max-sm:flex max-sm:flex-col max-sm:gap-3 max-sm:[&_tr:last-child]:border">
            {items.map((item) => (
              <TableRow key={item.id} className={CARD_ROW}>
                <TableCell
                  className={CARD_CELL}
                  data-label={t('approvals.list.type')}
                >
                  {typeLabel(item.request_type, t)}
                </TableCell>
                <TableCell
                  className={`whitespace-normal ${CARD_CELL}`}
                  data-label={t('approvals.list.request')}
                >
                  <RequestSummary item={item} />
                </TableCell>
                <TableCell
                  className={CARD_CELL}
                  data-label={t('approvals.list.requester')}
                >
                  {item.requester?.display_name ?? t('approvals.list.system')}
                </TableCell>
                <TableCell
                  className={CARD_CELL}
                  data-label={t('approvals.list.submittedAt')}
                >
                  {formatInstant(item.created_at, 'dateTime')}
                </TableCell>
                <TableCell
                  className={CARD_CELL}
                  data-label={t('approvals.list.priority')}
                >
                  <Badge>{t(`approvals.priority.${item.priority}`)}</Badge>
                </TableCell>
                <TableCell
                  className={CARD_CELL}
                  data-label={t('approvals.list.status')}
                >
                  <Badge>{t(`approvals.status.${item.status}`)}</Badge>
                </TableCell>
                <TableCell
                  className={CARD_CELL}
                  data-label={t('approvals.list.assignee')}
                >
                  {item.assignee?.display_name ??
                    t('approvals.assignee.unassigned')}
                </TableCell>
                <TableCell className={CARD_CELL}>
                  <div className="flex flex-wrap gap-2">
                    <ViewButton item={item} onOpen={onOpen} />
                    <ActionButtons item={item} onDecide={onDecide} />
                  </div>
                </TableCell>
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
            className="min-h-11"
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
            className="min-h-11"
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

/** "View details" (M8-01b A3), named with the row's summary. */
function ViewButton({
  item,
  onOpen,
}: {
  item: ApprovalRequestItem;
  onOpen: (item: ApprovalRequestItem, trigger: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const summary = approvalSummary(
    item,
    t,
    (instant) => formatInstant(instant, 'dateTime'),
    (localDate) => formatLocalDate(localDate),
  );
  return (
    <Button
      type="button"
      variant="outline"
      className="min-h-11"
      aria-label={t('approvals.list.viewFor', { summary })}
      onClick={(event) => onOpen(item, event.currentTarget)}
    >
      {t('approvals.list.view')}
    </Button>
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
  const report = progressReportReviewLink(item);
  if (report) {
    // #315 A5: progress reports are decided on their review page.
    return (
      <Link
        to={report.to}
        aria-label={
          report.queue
            ? undefined
            : t('approvals.progressReport.reviewFor', {
                student: report.student,
              })
        }
        className="inline-flex min-h-11 items-center rounded-md border border-border px-3 text-sm font-medium underline-offset-4 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring"
      >
        {report.queue
          ? t('approvals.progressReport.openQueue')
          : t('approvals.progressReport.review')}
      </Link>
    );
  }
  const review = itemReviewLink(item);
  if (review) {
    // #60 H2, #62 G2: session and change requests are decided on their
    // review pages.
    return (
      <Link
        to={review}
        aria-label={t('approvals.sessionRequest.reviewFor', { name })}
        className="inline-flex min-h-11 items-center rounded-md border border-border px-3 text-sm font-medium underline-offset-4 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring"
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
          className="min-h-11"
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
