import type { TFunction } from 'i18next';
import { useEffect, useRef, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { XIcon } from 'lucide-react';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useFormatters, type Formatters } from '@/lib/formatters';
import type { ApprovalAction, ApprovalRequestItem } from './api';
import { AssignControls, type AssigneeOptionsState } from './AssignControls';
import {
  approvalDetails,
  isOpenItem,
  ownPageLink,
  type DetailRow,
} from './details';
import {
  actionLabelKey,
  approvalSummary,
  availabilityChangeLink,
  reassignmentLink,
  typeLabel,
} from './summary';

// The detail panel of one approval request (M8-01b B, E1): a modal dialog,
// full screen on phones and a right-side panel from md up. It is labelled by
// its title (type and summary) and shows the request's status, priority,
// dates, people and reason, the type's own fields, the assignment controls
// of an open request, and its decisions (opening the DecisionDialog on top)
// or the link to the page where it is decided. Focus is trapped; Escape and
// the close button close it, and the page gives focus back. Presentational:
// the page passes the request's query state and the handlers.

export type PanelState =
  | { status: 'loading' }
  | { status: 'notFound' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; item: ApprovalRequestItem };

/** What the panel says after an assignment (M8-01b C2, E3). */
export type PanelNotice =
  | { kind: 'reloaded' }
  | { kind: 'stale' }
  | { kind: 'ineligible' }
  | { kind: 'error'; error: unknown };

interface DetailPanelProps {
  /** Whether `?item=` names a request. */
  open: boolean;
  state: PanelState;
  notice: PanelNotice | null;
  currentUserId: string | null;
  options: AssigneeOptionsState;
  assigning: boolean;
  onAssign: (assigneeId: string | null) => void;
  /** Opens the DecisionDialog for the item. */
  onDecide: (item: ApprovalRequestItem, action: ApprovalAction) => void;
  onClose: () => void;
  /** Gives focus back when the panel closed (the trigger, or the page
   * heading after a deep link). */
  onCloseFocus: () => void;
  /** The panel's heading, focusable: the DecisionDialog gives it focus
   * when its trigger is gone after a decision. */
  headingRef?: RefObject<HTMLHeadingElement | null>;
}

export function DetailPanel({
  open,
  state,
  notice,
  currentUserId,
  options,
  assigning,
  onAssign,
  onDecide,
  onClose,
  onCloseFocus,
  headingRef,
}: DetailPanelProps) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const ownHeading = useRef<HTMLHeadingElement>(null);
  const heading = headingRef ?? ownHeading;
  // QA D2: the control that sent an assignment is disabled while it is
  // sent, so the browser drops its focus to <body>, outside the modal (or
  // it is gone afterwards, like "Assign to me" once assigned). When the
  // assignment settles, focus goes back to that control if it is usable,
  // else to the panel's heading.
  const assignTrigger = useRef<Element | null>(null);
  useEffect(() => {
    if (assigning || assignTrigger.current === null) {
      return;
    }
    const trigger = assignTrigger.current;
    assignTrigger.current = null;
    const panel = heading.current?.closest('[role="dialog"]');
    if (!panel) {
      return;
    }
    const usable = (element: Element | null): element is HTMLElement =>
      element instanceof HTMLElement &&
      element.isConnected &&
      panel.contains(element) &&
      !(element as HTMLButtonElement).disabled;
    if (usable(document.activeElement)) {
      return;
    }
    if (usable(trigger)) {
      trigger.focus();
    } else {
      heading.current?.focus();
    }
  }, [assigning, heading]);
  const item = state.status === 'ready' ? state.item : null;
  const title = item
    ? panelTitle(item, t, formatters)
    : t('approvals.panel.genericTitle');

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          onClose();
        }
      }}
    >
      <DialogContent
        showCloseButton={false}
        aria-describedby={undefined}
        className="top-0 left-0 flex h-dvh max-h-dvh w-full max-w-none translate-x-0 translate-y-0 flex-col gap-6 overflow-y-auto rounded-none sm:max-w-none md:right-0 md:left-auto md:max-w-xl md:border-l md:border-border"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onCloseFocus();
        }}
      >
        <DialogHeader className="flex-row items-start justify-between gap-4">
          <DialogTitle
            ref={heading}
            tabIndex={-1}
            className="text-lg leading-snug font-semibold break-words outline-none"
          >
            {title}
          </DialogTitle>
          <DialogClose asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-11 shrink-0"
            >
              <XIcon aria-hidden="true" />
              <span className="sr-only">{t('approvals.panel.close')}</span>
            </Button>
          </DialogClose>
        </DialogHeader>
        <PanelBody
          state={state}
          notice={notice}
          currentUserId={currentUserId}
          options={options}
          assigning={assigning}
          onAssign={(assigneeId) => {
            assignTrigger.current = document.activeElement;
            onAssign(assigneeId);
          }}
          onDecide={onDecide}
        />
      </DialogContent>
    </Dialog>
  );
}

function panelTitle(
  item: ApprovalRequestItem,
  t: TFunction,
  formatters: Formatters,
): string {
  const type = typeLabel(item.request_type, t);
  const summary = approvalSummary(
    item,
    t,
    (instant) => formatters.formatInstant(instant, 'dateTime'),
    (localDate) => formatters.formatLocalDate(localDate),
  );
  return summary === item.request_type
    ? type
    : t('approvals.panel.title', { type, summary });
}

function PanelBody({
  state,
  notice,
  currentUserId,
  options,
  assigning,
  onAssign,
  onDecide,
}: Pick<
  DetailPanelProps,
  | 'state'
  | 'notice'
  | 'currentUserId'
  | 'options'
  | 'assigning'
  | 'onAssign'
  | 'onDecide'
>) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  if (state.status === 'loading') {
    return <LoadingState rows={4} />;
  }
  if (state.status === 'notFound') {
    return <p role="alert">{t('approvals.panel.notFound')}</p>;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { item } = state;
  const { formatInstant } = formatters;
  const details = approvalDetails(item, t, formatters);
  const open = isOpenItem(item);
  const facts: DetailRow[] = [
    {
      label: t('approvals.panel.status'),
      value: t(`approvals.status.${item.status}`),
    },
    {
      label: t('approvals.panel.priority'),
      value: t(`approvals.priority.${item.priority}`),
    },
    {
      label: t('approvals.panel.createdAt'),
      value: formatInstant(item.created_at, 'dateTime'),
    },
    {
      label: t('approvals.panel.requester'),
      value: item.requester?.display_name ?? t('approvals.list.system'),
    },
    {
      label: t('approvals.panel.assignee'),
      value: item.assignee?.display_name ?? t('approvals.assignee.unassigned'),
    },
    ...(item.resolved_at
      ? [
          {
            label: t('approvals.panel.resolvedAt'),
            value: formatInstant(item.resolved_at, 'dateTime'),
          },
        ]
      : []),
    ...(item.resolved_by
      ? [
          {
            label: t('approvals.panel.resolvedBy'),
            value: item.resolved_by.display_name,
          },
        ]
      : []),
    ...(item.reason
      ? [{ label: t('approvals.panel.reason'), value: item.reason }]
      : []),
  ];

  return (
    <div className="flex flex-col gap-6">
      {notice ? <Notice notice={notice} /> : null}
      <Facts rows={facts} />
      <section
        aria-labelledby={`${item.id}-details`}
        className="flex flex-col gap-2"
      >
        <h3 id={`${item.id}-details`} className="font-medium">
          {t('approvals.panel.details')}
        </h3>
        {details.known ? null : (
          <p className="text-sm">
            {t('approvals.panel.unknownType', { type: item.request_type })}
          </p>
        )}
        {details.rows.length > 0 ? (
          <Facts rows={details.rows} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {t('approvals.panel.noDetails')}
          </p>
        )}
      </section>
      {open ? (
        <section
          aria-labelledby={`${item.id}-assignment`}
          className="flex flex-col gap-2"
        >
          <h3 id={`${item.id}-assignment`} className="font-medium">
            {t('approvals.panel.assignment')}
          </h3>
          <AssignControls
            item={item}
            currentUserId={currentUserId}
            options={options}
            assigning={assigning}
            onAssign={onAssign}
          />
        </section>
      ) : null}
      <Actions item={item} onDecide={onDecide} />
    </div>
  );
}

function Notice({ notice }: { notice: PanelNotice }) {
  const { t } = useTranslation();
  if (notice.kind === 'reloaded') {
    return (
      <p role="alert" className="rounded-md border border-border p-3">
        {t('approvals.panel.reloaded')}
      </p>
    );
  }
  if (notice.kind === 'stale') {
    return <ErrorAlert error={null} message={t('approvals.panel.stale')} />;
  }
  if (notice.kind === 'ineligible') {
    return (
      <ErrorAlert error={null} message={t('approvals.panel.ineligible')} />
    );
  }
  return <ErrorAlert error={notice.error} />;
}

function Facts({ rows }: { rows: DetailRow[] }) {
  return (
    <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[minmax(8rem,auto)_1fr]">
      {rows.map((row, index) => (
        <div key={index} className="contents">
          <dt className="font-medium">{row.label}</dt>
          <dd className="min-w-0 break-words">
            {Array.isArray(row.value) ? (
              <ul className="list-disc ps-5">
                {row.value.map((value, position) => (
                  <li key={position}>{value}</li>
                ))}
              </ul>
            ) : (
              row.value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center self-start rounded-md border border-border px-3 text-sm font-medium underline-offset-4 outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring';

function Actions({
  item,
  onDecide,
}: {
  item: ApprovalRequestItem;
  onDecide: (item: ApprovalRequestItem, action: ApprovalAction) => void;
}) {
  const { t } = useTranslation();
  const ownPage = item.allowed_actions.length > 0 ? null : ownPageLink(item);
  // The pages an inbox-decided item also links to (#52 H3, #63 H2).
  const related = availabilityChangeLink(item)
    ? {
        to: availabilityChangeLink(item)!,
        label: t('approvals.availabilityChange.openAvailability'),
      }
    : reassignmentLink(item)
      ? {
          to: reassignmentLink(item)!,
          label: t('approvals.reassignment.openSession'),
        }
      : null;
  if (item.allowed_actions.length === 0 && !ownPage && !related) {
    return null;
  }
  return (
    <section
      className="flex flex-col gap-3"
      aria-label={t('approvals.list.actions')}
    >
      {item.allowed_actions.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {item.allowed_actions.map((action) => (
            <Button
              key={action}
              type="button"
              className="min-h-11"
              variant={action === 'approve' ? 'default' : 'outline'}
              onClick={() => onDecide(item, action)}
            >
              {t(actionLabelKey(item.request_type, action))}
            </Button>
          ))}
        </div>
      ) : null}
      {ownPage ? (
        <Link to={ownPage} className={LINK_CLASS}>
          {t('approvals.panel.reviewOnPage')}
        </Link>
      ) : null}
      {related ? (
        <Link to={related.to} className={LINK_CLASS}>
          {related.label}
        </Link>
      ) : null}
    </section>
  );
}
