import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Pagination } from '@/features/catalog/Pagination';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import type { FamilyStudent } from '@/features/my-schedule/api';
import { toLanguage } from '@/i18n/language';
import { cn } from '@/lib/utils';
import {
  PAGE_SIZE,
  type ChangeRequestItem,
  type ChangeRequestStatus,
  type StatusFilter,
} from './api';
import {
  outcomeText,
  requestLabel,
  whenText,
  type ListLabelContext,
} from './labels';
import { STATUS_FILTERS, staleWithdrawal, type ListState } from './rules';

// "My change requests" (#62b B2-B4), presentational: a status filter and,
// for a guardian with more than one child, a student filter (both in the
// URL); one card per request with the kind, the subject, the session's
// local date and time (center zone, named once), the proposed time of a
// reschedule, the student, the status as text (a labelled badge, never
// colour alone), the policy outcome ("as requested" until approved), the
// center's reason once decided and the note. Families never see a tutor
// (the API sends none). "Withdraw" (the requester, while pending) asks for
// confirmation; a stale withdrawal shows its message and the page
// refetches. Empty, loading and error states as #59's "My session
// requests".

export type ChangeRequestsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly ChangeRequestItem[];
      total: number;
      offset: number;
    };

export interface ChangeRequestsViewProps {
  state: ChangeRequestsState;
  filters: ListState;
  onFilters: (filters: ListState) => void;
  /** A guardian's children (the student filter shows from two). */
  students: readonly FamilyStudent[];
  timeZone: string;
  /** Withdraws; rejects with the error. */
  onWithdraw: (request: ChangeRequestItem) => Promise<void>;
  /** Refreshes the list after a stale withdrawal. */
  onStale: () => void;
  /** Stories: the confirmation open on this request. */
  initialWithdraw?: ChangeRequestItem;
}

const STATUS_STYLES: Record<ChangeRequestStatus, string> = {
  pending: 'border-border bg-muted text-foreground',
  approved: 'border-border bg-background text-foreground',
  rejected: 'border-destructive text-foreground',
  withdrawn: 'border-border bg-background text-muted-foreground',
};

interface Notice {
  requestId: string;
  key: NonNullable<ReturnType<typeof staleWithdrawal>>;
}

export function ChangeRequestsView({
  state,
  filters,
  onFilters,
  students,
  timeZone,
  onWithdraw,
  onStale,
  initialWithdraw,
}: ChangeRequestsViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const context: ListLabelContext = useMemo(
    () => ({ t, timeZone, language }),
    [t, timeZone, language],
  );
  const [withdrawing, setWithdrawing] = useState<ChangeRequestItem | null>(
    initialWithdraw ?? null,
  );
  const [notice, setNotice] = useState<Notice | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');
  // After a withdrawal its button is gone: the card takes the focus once the
  // refreshed list shows it (no longer withdrawable).
  const focusCard = useRef<string | null>(null);
  const cards = useRef(new Map<string, HTMLLIElement>());
  const items = state.status === 'ready' ? state.items : null;

  useEffect(() => {
    const target = focusCard.current;
    if (!target || !items) {
      return;
    }
    const item = items.find((candidate) => candidate.id === target);
    if (item && !item.can_withdraw) {
      cards.current.get(target)?.focus();
      focusCard.current = null;
    }
  }, [items]);

  async function confirmWithdraw() {
    const request = withdrawing;
    if (!request) {
      return;
    }
    try {
      await onWithdraw(request);
      setNotice(null);
      setFailure(null);
      setAnnouncement(
        t('changeRequests.list.withdrawn', {
          request: requestLabel(request, context),
        }),
      );
      focusCard.current = request.id;
    } catch (caught) {
      const key = staleWithdrawal(caught);
      if (key) {
        setNotice({ requestId: request.id, key });
        setFailure(null);
        onStale();
      } else {
        setNotice(null);
        setFailure(caught);
      }
    }
    setWithdrawing(null);
  }

  const showStudents = students.length > 1;
  const filtered = filters.status !== 'all' || filters.studentId !== null;

  let content;
  if (state.status === 'loading') {
    content = <LoadingState rows={4} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const list = state.items;
    const orphanNotice =
      notice && !list.some((item) => item.id === notice.requestId)
        ? notice
        : null;
    content = (
      <>
        {failure ? <ErrorAlert error={failure} /> : null}
        {orphanNotice ? (
          <p role="alert" className="rounded-md border border-destructive p-3">
            {t(orphanNotice.key)}
          </p>
        ) : null}
        {list.length === 0 ? (
          <div className="flex flex-col items-start gap-2">
            <p className="text-muted-foreground">
              {filtered
                ? t('changeRequests.list.emptyFiltered')
                : t('changeRequests.list.empty')}
            </p>
            {filtered ? null : (
              <Link
                to="/my-schedule"
                className="text-sm font-medium underline underline-offset-4"
              >
                {t('changeRequests.list.openSchedule')}
              </Link>
            )}
          </div>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {t('changeRequests.list.zone', { zone: timeZone })}
            </p>
            <ul className="grid min-w-0 gap-3 lg:grid-cols-2">
              {list.map((request) => (
                <RequestCard
                  key={request.id}
                  request={request}
                  context={context}
                  notice={
                    notice?.requestId === request.id ? t(notice.key) : null
                  }
                  onWithdraw={() => setWithdrawing(request)}
                  cardRef={(element) => {
                    if (element) {
                      cards.current.set(request.id, element);
                    } else {
                      cards.current.delete(request.id);
                    }
                  }}
                />
              ))}
            </ul>
            {state.total > PAGE_SIZE ? (
              <Pagination
                offset={state.offset}
                limit={PAGE_SIZE}
                shown={list.length}
                total={state.total}
                onPage={(page) => onFilters({ ...filters, page })}
              />
            ) : null}
          </>
        )}
      </>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      <div className="flex min-w-0 flex-wrap gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={`${id}-status`} className="text-sm font-medium">
            {t('changeRequests.list.statusFilter')}
          </label>
          <select
            id={`${id}-status`}
            value={filters.status}
            onChange={(event) =>
              onFilters({
                ...filters,
                status: event.target.value as StatusFilter,
                page: 1,
              })
            }
            className="h-9 min-w-0 rounded-md border border-input bg-background px-2.5 text-base md:text-sm"
          >
            {STATUS_FILTERS.map((status) => (
              <option key={status} value={status}>
                {status === 'all'
                  ? t('changeRequests.list.allStatuses')
                  : t(`scheduling.changeRequest.statuses.${status}`)}
              </option>
            ))}
          </select>
        </div>
        {showStudents ? (
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${id}-student`} className="text-sm font-medium">
              {t('changeRequests.list.studentFilter')}
            </label>
            <select
              id={`${id}-student`}
              value={filters.studentId ?? ''}
              onChange={(event) =>
                onFilters({
                  ...filters,
                  studentId: event.target.value || null,
                  page: 1,
                })
              }
              className="h-9 min-w-0 rounded-md border border-input bg-background px-2.5 text-base md:text-sm"
            >
              <option value="">{t('changeRequests.list.allStudents')}</option>
              {students.map((student) => (
                <option key={student.student_id} value={student.student_id}>
                  {student.display_name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>
      {content}
      <ConfirmDialog
        open={withdrawing !== null}
        title={t('changeRequests.list.withdrawTitle')}
        body={
          withdrawing
            ? t('changeRequests.list.withdrawBody', {
                request: requestLabel(withdrawing, context),
              })
            : ''
        }
        confirmLabel={t('changeRequests.list.withdraw')}
        destructive
        onConfirm={confirmWithdraw}
        onCancel={() => setWithdrawing(null)}
      />
    </div>
  );
}

function RequestCard({
  request,
  context,
  notice,
  onWithdraw,
  cardRef,
}: {
  request: ChangeRequestItem;
  context: ListLabelContext;
  notice: string | null;
  onWithdraw: () => void;
  cardRef: (element: HTMLLIElement | null) => void;
}) {
  const { t } = context;
  const session = request.class_session;
  return (
    <li
      tabIndex={-1}
      ref={cardRef}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <h2 className="min-w-0 font-medium break-words">
          {t('changeRequests.list.heading', {
            kind: t(`scheduling.changeRequest.kinds.${request.kind}`),
            subject: session.subject.name,
          })}
        </h2>
        <span
          className={cn(
            'rounded-md border px-2 py-0.5 text-xs font-medium',
            STATUS_STYLES[request.status],
          )}
        >
          {t('changeRequests.list.statusText', {
            status: t(`scheduling.changeRequest.statuses.${request.status}`),
          })}
        </span>
      </div>
      <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="font-medium">{t('changeRequests.list.session')}</dt>
        <dd className="min-w-0 break-words">
          {whenText(session.starts_at, session.ends_at, context)}
        </dd>
        {request.kind === 'reschedule' &&
        request.proposed_starts_at &&
        request.proposed_ends_at ? (
          <>
            <dt className="font-medium">{t('changeRequests.list.proposed')}</dt>
            <dd className="min-w-0 break-words">
              {whenText(
                request.proposed_starts_at,
                request.proposed_ends_at,
                context,
              )}
            </dd>
          </>
        ) : null}
        {request.student ? (
          <>
            <dt className="font-medium">{t('changeRequests.list.student')}</dt>
            <dd className="min-w-0 break-words">
              {request.student.display_name}
            </dd>
          </>
        ) : null}
        <dt className="font-medium">{t('changeRequests.list.outcome')}</dt>
        <dd className="min-w-0 break-words">{outcomeText(request, context)}</dd>
        {request.decision?.reason ? (
          <>
            <dt className="font-medium">{t('changeRequests.list.reason')}</dt>
            <dd className="min-w-0 break-words whitespace-pre-line">
              {request.decision.reason}
            </dd>
          </>
        ) : null}
        {request.note ? (
          <>
            <dt className="font-medium">{t('changeRequests.list.note')}</dt>
            <dd className="min-w-0 break-words whitespace-pre-line">
              {request.note}
            </dd>
          </>
        ) : null}
      </dl>
      {notice ? (
        <p role="alert" className="text-sm text-destructive">
          {notice}
        </p>
      ) : null}
      {request.can_withdraw ? (
        <div>
          <Button
            type="button"
            variant="outline"
            aria-label={t('changeRequests.list.withdrawFor', {
              request: requestLabel(request, context),
            })}
            onClick={onWithdraw}
          >
            {t('changeRequests.list.withdraw')}
          </Button>
        </div>
      ) : null}
    </li>
  );
}
