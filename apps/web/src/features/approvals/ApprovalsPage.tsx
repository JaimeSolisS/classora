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
import {
  APPROVAL_TYPES,
  APPROVALS_QUERY_KEY,
  approvalsKey,
  ASSIGNEE_FILTERS,
  decideApproval,
  fetchApprovals,
  STATUS_FILTERS,
  type ApprovalAction,
  type ApprovalFilters,
} from './api';
import { ApprovalsTable, type ApprovalsState } from './ApprovalsTable';
import { DecisionDialog, type PendingDecision } from './DecisionDialog';
import { filtersFromParams, offsetFromParams, paramsFor } from './filters';
import { typeLabel } from './summary';

// /approvals (#40 F; the basic list view, M8-01 #118 builds the full inbox):
// the approval requests of the types the user reviews, filtered by status
// (Open by default), type and assignee, all reflected in the URL. Approve
// asks for confirmation; Reject and Request changes ask for a reason. A
// decision refreshes the list and is announced politely.

const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring md:text-sm';

const DONE_KEYS = {
  approve: 'approvals.done.approved',
  reject: 'approvals.done.rejected',
  request_changes: 'approvals.done.changesRequested',
} as const;

export function ApprovalsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('approvals.title'));
  const queryClient = useQueryClient();
  const id = useId();
  const statusField = useRef<HTMLSelectElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = filtersFromParams(searchParams);
  const offset = offsetFromParams(searchParams);
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [done, setDone] = useState<ApprovalAction | null>(null);

  const list = useQuery({
    queryKey: approvalsKey(filters, offset),
    queryFn: ({ signal }) => fetchApprovals(filters, offset, signal),
    placeholderData: keepPreviousData,
  });

  function refreshList() {
    void queryClient.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
  }

  function change(next: Partial<ApprovalFilters>) {
    setDone(null);
    // A new filter starts on the first page.
    setSearchParams(paramsFor({ ...filters, ...next }));
  }

  async function confirm({ item, action }: PendingDecision, reason: string) {
    setDone(null);
    try {
      await decideApproval(item.id, action, reason);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.code === 'approval_transition_invalid'
      ) {
        // Someone else decided first: the dialog says so; the list updates.
        refreshList();
      }
      throw caught;
    }
    setPending(null);
    setDone(action);
    refreshList();
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

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('approvals.title')}</h1>
        <p>{t('approvals.intro')}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
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
      </div>
      <div role="status">{done ? <p>{t(DONE_KEYS[done])}</p> : null}</div>
      <ApprovalsTable
        state={listState}
        onDecide={(item, action) => {
          setDone(null);
          setPending({ item, action });
        }}
        onPage={(next) => setSearchParams(paramsFor(filters, next))}
      />
      <DecisionDialog
        pending={pending}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
        fallbackFocus={statusField}
      />
    </div>
  );
}
