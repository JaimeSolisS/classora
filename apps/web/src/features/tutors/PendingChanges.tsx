import { useId, useRef, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useFormatters } from '@/lib/formatters';
import type { AvailabilityChangeRequest } from './api';
import {
  affectedSessionText,
  describeChange,
  moreCount,
} from './changeRequests';

// The availability changes that wait for approval (M4-18 #63 H1), for the
// tutor and staff: what each changes, how many scheduled classes it
// affects, who sent it and when, and (editors) Withdraw behind a
// confirmation. Then the recently decided ones, with a rejection's reason.
// Also `SentForApprovalNotice`: the polite status after a `202`.

/** How many affected sessions the notice lists. */
export const NOTICE_SESSIONS = 3;

export interface PendingChangesProps {
  pending: readonly AvailabilityChangeRequest[];
  recent: readonly AvailabilityChangeRequest[];
  canEdit: boolean;
  /** Withdraws the request; rejects with the error to show. The page
   * refreshes the availability either way. */
  onWithdraw: (request: AvailabilityChangeRequest) => Promise<void>;
  /** Called after a withdrawal, for the section's status message. */
  onWithdrawn: () => void;
  /** Gets focus when a dialog closes and its trigger is gone (the
   * withdrawn request left the list). */
  fallbackFocus?: RefObject<HTMLElement | null>;
}

export function PendingChanges({
  pending,
  recent,
  canEdit,
  onWithdraw,
  onWithdrawn,
  fallbackFocus,
}: PendingChangesProps) {
  const { t } = useTranslation();
  const id = useId();
  const { formatInstant, formatLocalDate } = useFormatters();
  const [withdrawing, setWithdrawing] =
    useState<AvailabilityChangeRequest | null>(null);
  const describe = (request: AvailabilityChangeRequest) =>
    describeChange(request, t, (localDate) => formatLocalDate(localDate));

  if (pending.length === 0 && recent.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-4">
      {pending.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h3 id={`${id}-pending`} className="text-base font-semibold">
            {t('tutors.availability.changes.pendingTitle')}
          </h3>
          <p className="text-sm text-muted-foreground">
            {t('tutors.availability.changes.pendingHelp')}
          </p>
          <ul aria-labelledby={`${id}-pending`} className="flex flex-col gap-2">
            {pending.map((request) => {
              const text = describe(request);
              return (
                <li
                  key={request.id}
                  data-change-request-id={request.id}
                  className="flex flex-col gap-1 rounded-md border border-border p-3"
                >
                  <p className="font-medium break-words">{text}</p>
                  <p className="text-sm">
                    {t('tutors.availability.changes.affected', {
                      count: request.affected_count,
                    })}
                  </p>
                  <p className="text-sm text-muted-foreground break-words">
                    {t('tutors.availability.changes.requestedBy', {
                      name: request.requested_by.display_name,
                      when: formatInstant(request.created_at, 'dateTime'),
                    })}
                  </p>
                  {canEdit ? (
                    <div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t(
                          'tutors.availability.changes.withdrawFor',
                          {
                            change: text,
                          },
                        )}
                        onClick={() => setWithdrawing(request)}
                      >
                        {t('tutors.availability.changes.withdraw')}
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {recent.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h3 id={`${id}-recent`} className="text-base font-semibold">
            {t('tutors.availability.changes.recentTitle')}
          </h3>
          <ul aria-labelledby={`${id}-recent`} className="flex flex-col gap-2">
            {recent.map((request) => (
              <li
                key={request.id}
                data-change-request-id={request.id}
                className="flex flex-col gap-1 rounded-md border border-border p-3"
              >
                <p className="font-medium break-words">{describe(request)}</p>
                <p className="text-sm">
                  {t(`tutors.availability.changes.statuses.${request.status}`, {
                    when: request.resolved_at
                      ? formatInstant(request.resolved_at, 'dateTime')
                      : '',
                  })}
                </p>
                {request.status === 'rejected' && request.rejection_reason ? (
                  <p className="text-sm break-words">
                    {t('tutors.availability.changes.reason', {
                      reason: request.rejection_reason,
                    })}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <WithdrawDialog
        request={withdrawing}
        describe={describe}
        onConfirm={async (request) => {
          await onWithdraw(request);
          setWithdrawing(null);
          onWithdrawn();
        }}
        onKeep={() => setWithdrawing(null)}
        fallbackFocus={fallbackFocus}
      />
    </div>
  );
}

/** The confirmation of Withdraw; an API error (409) shows in the dialog. */
function WithdrawDialog({
  request,
  describe,
  onConfirm,
  onKeep,
  fallbackFocus,
}: {
  request: AvailabilityChangeRequest | null;
  describe: (request: AvailabilityChangeRequest) => string;
  onConfirm: (request: AvailabilityChangeRequest) => Promise<void>;
  onKeep: () => void;
  fallbackFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const returnFocus = useRef<Element | null>(null);

  async function confirm() {
    if (sending.current || !request) {
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(request);
    } catch (caught) {
      setError(caught);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={request !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          setError(null);
          onKeep();
        }
      }}
    >
      {request ? (
        <DialogContent
          onOpenAutoFocus={() => {
            returnFocus.current = document.activeElement;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus.current;
            if (target instanceof HTMLElement && target.isConnected) {
              target.focus();
            } else {
              fallbackFocus?.current?.focus();
            }
          }}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>
              {t('tutors.availability.changes.withdrawTitle')}
            </DialogTitle>
            <DialogDescription>
              {t('tutors.availability.changes.withdrawDescription', {
                change: describe(request),
              })}
            </DialogDescription>
          </DialogHeader>
          {error ? <ErrorAlert error={error} /> : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setError(null);
                onKeep();
              }}
            >
              {t('tutors.availability.changes.withdrawKeep')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? (
                <Spinner
                  label={t('tutors.availability.changes.withdrawing')}
                  showLabel
                />
              ) : (
                t('tutors.availability.changes.withdrawConfirm')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

/** The polite status after a change was sent for approval (`202`): how
 * many scheduled classes it affects and the first of them. */
export function SentForApprovalNotice({
  request,
}: {
  request: AvailabilityChangeRequest | null;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  return (
    <div role="status" className="text-sm">
      {request ? (
        <div className="flex flex-col gap-1 rounded-md border border-border p-3">
          <p className="font-medium">
            {t('tutors.availability.changes.sent', {
              count: request.affected_count,
            })}
          </p>
          <ul className="list-disc ps-5">
            {request.affected_sessions
              .slice(0, NOTICE_SESSIONS)
              .map((session) => (
                <li key={session.id} className="break-words">
                  {affectedSessionText(session, t, formatInstant)}
                </li>
              ))}
          </ul>
          {moreCount(request.affected_count, NOTICE_SESSIONS) > 0 ? (
            <p>
              {t('tutors.availability.changes.more', {
                count: moreCount(request.affected_count, NOTICE_SESSIONS),
              })}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
