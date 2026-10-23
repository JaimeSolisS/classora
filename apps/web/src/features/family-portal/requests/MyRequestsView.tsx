import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import type { ErrorMessageKey } from '@/app/errors';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { toLanguage } from '@/i18n/language';
import { cn } from '@/lib/utils';
import type { SessionRequestItem, SessionRequestStatus } from './api';
import { slotTimes, staleWithdrawal } from './rules';

// "My session requests" (#59 F4), presentational: one card per request
// (stacked on phones, two columns on wide screens) with the student, the
// subject and level, the local date and times (center zone, named once),
// the length, the status as text (never colour alone) and the note.
// "Withdraw" (the requester, while pending) asks for confirmation. A
// `409 version_conflict` / `session_request_not_pending` shows its message
// on the card (or above the list once the refreshed list no longer has it)
// and the page refreshes the list. An empty state links to "Request a
// session".

export type MyRequestsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly SessionRequestItem[] };

export interface MyRequestsViewProps {
  state: MyRequestsState;
  timeZone: string;
  /** Withdraws; rejects with the error. */
  onWithdraw: (request: SessionRequestItem) => Promise<void>;
  /** Refreshes the list after a stale withdrawal. */
  onStale: () => void;
  /** Stories: the confirmation open on this request. */
  initialWithdraw?: SessionRequestItem;
}

const STATUS_STYLES: Record<SessionRequestStatus, string> = {
  pending: 'border-border bg-muted text-foreground',
  confirmed: 'border-border bg-background text-foreground',
  rejected: 'border-destructive text-foreground',
  withdrawn: 'border-border bg-background text-muted-foreground',
};

interface Notice {
  requestId: string;
  key: ErrorMessageKey;
}

export function MyRequestsView({
  state,
  timeZone,
  onWithdraw,
  onStale,
  initialWithdraw,
}: MyRequestsViewProps) {
  const { t, i18n } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const language = toLanguage(i18n.resolvedLanguage);
  const [withdrawing, setWithdrawing] = useState<SessionRequestItem | null>(
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

  function label(request: SessionRequestItem): string {
    const times = slotTimes(request, timeZone, language);
    return t('sessionRequests.list.label', {
      subject: request.subject.name,
      date: times.date,
      start: times.start,
    });
  }

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
        t('sessionRequests.list.withdrawn', { request: label(request) }),
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

  if (state.status === 'loading') {
    return <LoadingState rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }

  const list = state.items;
  const orphanNotice =
    notice && !list.some((item) => item.id === notice.requestId)
      ? notice
      : null;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {failure ? <ErrorAlert error={failure} /> : null}
      {orphanNotice ? (
        <p role="alert" className="rounded-md border border-destructive p-3">
          {tErrors(orphanNotice.key)}
        </p>
      ) : null}
      {list.length === 0 ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-muted-foreground">
            {t('sessionRequests.list.empty')}
          </p>
          <Link
            to="/requests/new"
            className="text-sm font-medium underline underline-offset-4"
          >
            {t('sessionRequests.list.requestOne')}
          </Link>
        </div>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {t('sessionRequests.new.zone', { zone: timeZone })}
          </p>
          <ul className="grid min-w-0 gap-3 lg:grid-cols-2">
            {list.map((request) => {
              const times = slotTimes(request, timeZone, language);
              return (
                <li
                  key={request.id}
                  tabIndex={-1}
                  ref={(element) => {
                    if (element) {
                      cards.current.set(request.id, element);
                    } else {
                      cards.current.delete(request.id);
                    }
                  }}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                    <h2 className="min-w-0 font-medium break-words">
                      {t('sessionRequests.subject', {
                        subject: request.subject.name,
                        level: request.subject.education_level_name,
                      })}
                    </h2>
                    <span
                      className={cn(
                        'rounded-md border px-2 py-0.5 text-xs font-medium',
                        STATUS_STYLES[request.status],
                      )}
                    >
                      {t(`sessionRequests.status.${request.status}`)}
                    </span>
                  </div>
                  <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                    <dt className="font-medium">
                      {t('sessionRequests.confirm.student')}
                    </dt>
                    <dd className="min-w-0 break-words">
                      {request.student.display_name}
                    </dd>
                    <dt className="font-medium">
                      {t('sessionRequests.confirm.when')}
                    </dt>
                    <dd className="min-w-0 break-words">
                      {t('sessionRequests.when', {
                        date: times.date,
                        start: times.start,
                        end: times.end,
                      })}
                    </dd>
                    <dt className="font-medium">
                      {t('sessionRequests.confirm.length')}
                    </dt>
                    <dd>
                      {t('sessionRequests.minutes', {
                        minutes: request.duration_minutes,
                      })}
                    </dd>
                    {request.note ? (
                      <>
                        <dt className="font-medium">
                          {t('sessionRequests.list.note')}
                        </dt>
                        <dd className="min-w-0 break-words">{request.note}</dd>
                      </>
                    ) : null}
                  </dl>
                  {notice?.requestId === request.id ? (
                    <p role="alert" className="text-sm text-destructive">
                      {tErrors(notice.key)}
                    </p>
                  ) : null}
                  {request.can_withdraw ? (
                    <div>
                      <Button
                        type="button"
                        variant="outline"
                        aria-label={t('sessionRequests.list.withdrawFor', {
                          request: label(request),
                        })}
                        onClick={() => setWithdrawing(request)}
                      >
                        {t('sessionRequests.list.withdraw')}
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <ConfirmDialog
        open={withdrawing !== null}
        title={t('sessionRequests.list.withdrawTitle')}
        body={
          withdrawing
            ? t('sessionRequests.list.withdrawBody', {
                request: label(withdrawing),
              })
            : ''
        }
        confirmLabel={t('sessionRequests.list.withdraw')}
        destructive
        onConfirm={confirmWithdraw}
        onCancel={() => setWithdrawing(null)}
      />
    </div>
  );
}
