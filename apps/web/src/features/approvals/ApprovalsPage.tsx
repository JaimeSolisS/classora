import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import { useSession } from '@/features/auth/useSession';
import {
  APPROVAL_TYPES,
  APPROVALS_QUERY_KEY,
  approvalKey,
  approvalsKey,
  ASSIGNEE_FILTERS,
  assignApproval,
  assigneeOptionsKey,
  decideApproval,
  fetchApproval,
  fetchApprovals,
  fetchAssigneeOptions,
  PRIORITY_FILTERS,
  SORTS,
  STATUS_FILTERS,
  type ApprovalAction,
  type ApprovalFilters,
  type ApprovalRequestItem,
} from './api';
import type { AssigneeOptionsState } from './AssignControls';
import { ApprovalsTable, type ApprovalsState } from './ApprovalsTable';
import { DecisionDialog, type PendingDecision } from './DecisionDialog';
import { DetailPanel, type PanelNotice, type PanelState } from './DetailPanel';
import { isOpenItem } from './details';
import {
  filtersFromParams,
  isFiltered,
  isRequestId,
  itemFromParams,
  offsetFromParams,
  paramsFor,
  withItem,
} from './filters';
import { typeLabel } from './summary';

// /approvals (#40 F; M8-01b): the approval requests of the types the user
// reviews, filtered by status (Open by default), type, assignee and
// priority and sorted (priority first by default), all reflected in the
// URL. "View details" opens the request's panel (`?item=<id>`, also a deep
// link from a notification), where it is assigned and decided, or followed
// to its own page. Approve asks for confirmation; Reject and Request changes
// ask for a reason. A decision or an assignment refreshes the list and is
// announced politely in one live region.

const SELECT_CLASS =
  'min-h-11 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring md:text-sm';

const DONE_KEYS = {
  approve: 'approvals.done.approved',
  reject: 'approvals.done.rejected',
  request_changes: 'approvals.done.changesRequested',
} as const;

/** What the live region says: a decision, or an assignment (C1). */
type Done =
  | { kind: 'decision'; action: ApprovalAction }
  | { kind: 'assigned'; name: string | null };

export function ApprovalsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('approvals.title'));
  const queryClient = useQueryClient();
  const session = useSession();
  const currentUserId = session.status === 'signedIn' ? session.user.id : null;
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const statusField = useRef<HTMLSelectElement>(null);
  const panelHeading = useRef<HTMLHeadingElement>(null);
  // The "View details" button that opened the panel (focus goes back to it).
  const trigger = useRef<HTMLElement | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = filtersFromParams(searchParams);
  const offset = offsetFromParams(searchParams);
  const itemId = itemFromParams(searchParams);
  const requestId = itemId && isRequestId(itemId) ? itemId : null;
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [notice, setNotice] = useState<PanelNotice | null>(null);
  const [assigning, setAssigning] = useState(false);
  // A request an assignment answered 404 for (C2): shown as not found.
  const [goneId, setGoneId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: approvalsKey(filters, offset),
    queryFn: ({ signal }) => fetchApprovals(filters, offset, signal),
    placeholderData: keepPreviousData,
  });

  const detail = useQuery({
    queryKey: approvalKey(requestId ?? ''),
    queryFn: ({ signal }) => fetchApproval(requestId!, signal),
    enabled: requestId !== null,
  });
  const shown =
    detail.data && detail.data.id === requestId ? detail.data : null;

  const options = useQuery({
    queryKey: assigneeOptionsKey(requestId ?? ''),
    queryFn: ({ signal }) => fetchAssigneeOptions(requestId!, signal),
    enabled: requestId !== null && shown !== null && isOpenItem(shown),
  });

  function refreshList() {
    void queryClient.invalidateQueries({
      queryKey: [...APPROVALS_QUERY_KEY, 'list'],
    });
  }

  function refreshAll() {
    void queryClient.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
  }

  function change(next: Partial<ApprovalFilters>) {
    setDone(null);
    // A new filter starts on the first page.
    setSearchParams(paramsFor({ ...filters, ...next }));
  }

  function openPanel(item: ApprovalRequestItem, from: HTMLElement) {
    trigger.current = from;
    setDone(null);
    setNotice(null);
    setGoneId(null);
    setSearchParams(withItem(searchParams, item.id));
  }

  function closePanel() {
    setNotice(null);
    setGoneId(null);
    setSearchParams(withItem(searchParams, null));
  }

  function focusAfterClose() {
    const target = trigger.current;
    trigger.current = null;
    if (target instanceof HTMLElement && target.isConnected) {
      target.focus();
    } else {
      heading.current?.focus();
    }
  }

  async function confirm({ item, action }: PendingDecision, reason: string) {
    setDone(null);
    let decided: ApprovalRequestItem;
    try {
      decided = await decideApproval(item.id, action, reason);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.code === 'approval_transition_invalid'
      ) {
        // Someone else decided first: the dialog says so; the list and the
        // open panel update.
        refreshAll();
      }
      throw caught;
    }
    if (requestId === item.id) {
      queryClient.setQueryData(approvalKey(item.id), decided);
    }
    setPending(null);
    setDone({ kind: 'decision', action });
    refreshAll();
  }

  async function assign(assigneeId: string | null) {
    if (!shown || assigning) {
      return;
    }
    const item = shown;
    setAssigning(true);
    setNotice(null);
    setDone(null);
    try {
      const updated = await assignApproval(item.id, assigneeId, item.version);
      queryClient.setQueryData(approvalKey(item.id), updated);
      setDone({
        kind: 'assigned',
        name: updated.assignee?.display_name ?? null,
      });
      refreshList();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (code === 'version_conflict' || code === 'approval_request_closed') {
        // E3: reload the request; a failed reload keeps what is shown and
        // says it may be out of date, never that it reloaded.
        const reloaded = await detail.refetch();
        setNotice(reloaded.isError ? { kind: 'stale' } : { kind: 'reloaded' });
        refreshList();
      } else if (code === 'approval_assignee_ineligible') {
        void options.refetch();
        setNotice({ kind: 'ineligible' });
      } else if (caught instanceof ApiError && caught.status === 404) {
        setGoneId(item.id);
        refreshList();
      } else {
        setNotice({ kind: 'error', error: caught });
      }
    } finally {
      setAssigning(false);
    }
  }

  const listState: ApprovalsState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  const notFound =
    itemId !== null &&
    (requestId === null ||
      goneId === requestId ||
      (detail.error instanceof ApiError && detail.error.status === 404));
  const panelState: PanelState = notFound
    ? { status: 'notFound' }
    : shown
      ? { status: 'ready', item: shown }
      : detail.isError
        ? {
            status: 'error',
            error: detail.error,
            onRetry: () => void detail.refetch(),
          }
        : { status: 'loading' };
  const optionsState: AssigneeOptionsState = options.data
    ? { status: 'ready', items: options.data }
    : options.isError
      ? {
          status: 'error',
          error: options.error,
          onRetry: () => void options.refetch(),
        }
      : { status: 'loading' };

  let announcement: string | null = null;
  if (done?.kind === 'decision') {
    announcement = t(DONE_KEYS[done.action]);
  } else if (done?.kind === 'assigned') {
    announcement = done.name
      ? t('approvals.assign.assignedTo', { name: done.name })
      : t('approvals.assign.unassigned');
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {t('approvals.title')}
        </h1>
        <p>{t('approvals.intro')}</p>
      </div>
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-status`} className="text-sm font-medium">
              {t('approvals.filter.status')}
            </label>
            <select
              id={`${id}-status`}
              ref={statusField}
              value={filters.status}
              className={SELECT_CLASS}
              onChange={(event) =>
                change({
                  status: event.target.value as ApprovalFilters['status'],
                })
              }
            >
              {STATUS_FILTERS.map((value) => (
                <option key={value} value={value}>
                  {t(`approvals.status.${value}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-type`} className="text-sm font-medium">
              {t('approvals.filter.type')}
            </label>
            <select
              id={`${id}-type`}
              value={filters.type ?? ''}
              className={SELECT_CLASS}
              onChange={(event) => change({ type: event.target.value || null })}
            >
              <option value="">{t('approvals.filter.allTypes')}</option>
              {APPROVAL_TYPES.map((type) => (
                <option key={type} value={type}>
                  {typeLabel(type, t)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-assignee`} className="text-sm font-medium">
              {t('approvals.filter.assignee')}
            </label>
            <select
              id={`${id}-assignee`}
              value={filters.assignee}
              className={SELECT_CLASS}
              onChange={(event) =>
                change({
                  assignee: event.target.value as ApprovalFilters['assignee'],
                })
              }
            >
              {ASSIGNEE_FILTERS.map((value) => (
                <option key={value} value={value}>
                  {t(`approvals.assignee.${value}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-priority`} className="text-sm font-medium">
              {t('approvals.filter.priority')}
            </label>
            <select
              id={`${id}-priority`}
              value={filters.priority ?? ''}
              className={SELECT_CLASS}
              onChange={(event) =>
                change({
                  priority:
                    (event.target.value as ApprovalFilters['priority']) || null,
                })
              }
            >
              <option value="">{t('approvals.filter.allPriorities')}</option>
              {PRIORITY_FILTERS.map((value) => (
                <option key={value} value={value}>
                  {t(`approvals.priority.${value}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-sort`} className="text-sm font-medium">
              {t('approvals.filter.sort')}
            </label>
            <select
              id={`${id}-sort`}
              value={filters.sort}
              className={SELECT_CLASS}
              onChange={(event) =>
                change({
                  sort: event.target.value as ApprovalFilters['sort'],
                })
              }
            >
              {SORTS.map((value) => (
                <option key={value} value={value}>
                  {t(`approvals.sort.${value}`)}
                </option>
              ))}
            </select>
          </div>
        </div>
        {isFiltered(filters) ? (
          <Button
            type="button"
            variant="outline"
            className="min-h-11 self-start"
            onClick={() => {
              setDone(null);
              setSearchParams(new URLSearchParams());
              // The button goes away: focus the first filter instead.
              statusField.current?.focus();
            }}
          >
            {t('approvals.filter.clear')}
          </Button>
        ) : null}
        <p aria-live="polite" aria-atomic="true" className="text-sm">
          {list.data
            ? t('approvals.list.count', { count: list.data.total })
            : null}
        </p>
      </div>
      <div role="status" aria-live="polite">
        {announcement ? <p>{announcement}</p> : null}
      </div>
      <ApprovalsTable
        state={listState}
        onDecide={(item, action) => {
          setDone(null);
          setPending({ item, action });
        }}
        onOpen={openPanel}
        onPage={(next) => setSearchParams(paramsFor(filters, next))}
      />
      <DetailPanel
        open={itemId !== null}
        state={panelState}
        notice={notice}
        currentUserId={currentUserId}
        options={optionsState}
        assigning={assigning}
        onAssign={(assigneeId) => void assign(assigneeId)}
        onDecide={(item, action) => {
          setDone(null);
          setPending({ item, action });
        }}
        onClose={closePanel}
        onCloseFocus={focusAfterClose}
        headingRef={panelHeading}
      />
      <DecisionDialog
        pending={pending}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
        fallbackFocus={itemId !== null ? panelHeading : statusField}
      />
    </div>
  );
}
