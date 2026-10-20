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
import { FieldError } from '@/features/auth/FieldError';
import { roleNameKey } from '@/features/users/roles';
import {
  reasonRequired,
  type ApprovalAction,
  type ApprovalRequestItem,
} from './api';
import { approvalSummary, registrationSubject } from './summary';

// The confirmation of a decision (#40 F4; moved from #169 G3). Approve asks
// for confirmation; Reject and Request changes have a reason field, required
// when the action needs it (at most 1000 characters). Errors from the API
// (a handler error such as guardian_consent_required, or
// approval_transition_invalid) show in the dialog's alert region. Without
// any API call: the page passes `onConfirm`, stories pass a stub. While the
// decision is sent the confirm button is disabled and shows the Spinner.

export const REASON_MAX_LENGTH = 1000;

export interface PendingDecision {
  item: ApprovalRequestItem;
  action: ApprovalAction;
}

interface DecisionDialogProps {
  /** The decision to confirm, or null when closed. */
  pending: PendingDecision | null;
  /** Sends the decision. Rejects with the error to show in the dialog. */
  onConfirm: (pending: PendingDecision, reason: string) => Promise<void>;
  onCancel: () => void;
  /** Gets focus on close when the element that opened the dialog is gone. */
  fallbackFocus?: RefObject<HTMLElement | null>;
}

export function DecisionDialog({
  pending,
  onConfirm,
  onCancel,
  fallbackFocus,
}: DecisionDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={pending !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {pending ? (
        <DecisionDialogContent
          key={`${pending.item.id}-${pending.action}`}
          pending={pending}
          onConfirm={onConfirm}
          onCancel={onCancel}
          sendingRef={sending}
          fallbackFocus={fallbackFocus}
        />
      ) : null}
    </Dialog>
  );
}

const TITLE_KEYS = {
  approve: 'approvals.dialog.approveTitle',
  reject: 'approvals.dialog.rejectTitle',
  request_changes: 'approvals.dialog.requestChangesTitle',
} as const;

const CONFIRM_KEYS = {
  approve: 'approvals.actions.approve',
  reject: 'approvals.actions.reject',
  request_changes: 'approvals.actions.requestChanges',
} as const;

const SENDING_KEYS = {
  approve: 'approvals.dialog.approving',
  reject: 'approvals.dialog.rejecting',
  request_changes: 'approvals.dialog.requestingChanges',
} as const;

function DecisionDialogContent({
  pending,
  onConfirm,
  onCancel,
  sendingRef,
  fallbackFocus,
}: {
  pending: PendingDecision;
  onConfirm: (pending: PendingDecision, reason: string) => Promise<void>;
  onCancel: () => void;
  sendingRef: RefObject<boolean>;
  fallbackFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const reasonId = `${id}-reason`;
  const reasonErrorId = `${id}-reason-error`;
  const reasonField = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState('');
  const [reasonError, setReasonError] = useState<string | undefined>();
  const [error, setError] = useState<unknown>(null);
  const [sending, setSending] = useState(false);
  // The element focused before the dialog opened (it opens without a
  // trigger), to give focus back on close.
  const returnFocus = useRef<Element | null>(null);
  const { item, action } = pending;
  const summary = approvalSummary(item, t);
  const registration = registrationSubject(item);
  const withReason = action !== 'approve';
  const required = reasonRequired(item.request_type, action);

  let description: string;
  if (action === 'approve') {
    description = registration
      ? t('approvals.dialog.registrationApprove', {
          name: registration.display_name,
          role: t(roleNameKey(registration.requested_role)),
        })
      : t('approvals.dialog.approveConfirm');
  } else if (action === 'reject') {
    description = registration
      ? t('approvals.dialog.registrationReject', {
          username: registration.username,
        })
      : t('approvals.dialog.rejectConfirm');
  } else {
    description = t('approvals.dialog.requestChangesConfirm');
  }

  async function confirm() {
    if (sendingRef.current) {
      return;
    }
    if (withReason && required && !reason.trim()) {
      setReasonError(t('approvals.dialog.reasonRequired'));
      reasonField.current?.focus();
      return;
    }
    setReasonError(undefined);
    sendingRef.current = true;
    setSending(true);
    setError(null);
    try {
      await onConfirm(pending, reason);
    } catch (caught) {
      setError(caught);
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  return (
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
        <DialogTitle>{t(TITLE_KEYS[action], { summary })}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      {withReason ? (
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {required
              ? t('approvals.dialog.reasonRequiredLabel')
              : t('approvals.dialog.reasonOptionalLabel')}
          </label>
          <textarea
            id={reasonId}
            ref={reasonField}
            value={reason}
            rows={3}
            maxLength={REASON_MAX_LENGTH}
            required={required}
            aria-invalid={reasonError ? true : undefined}
            aria-describedby={reasonError ? reasonErrorId : undefined}
            disabled={sending}
            className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
            onChange={(event) => setReason(event.target.value)}
          />
          <FieldError id={reasonErrorId} message={reasonError} />
        </div>
      ) : null}
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={sending}
          onClick={onCancel}
        >
          {t('approvals.dialog.cancel')}
        </Button>
        <Button
          type="button"
          variant={action === 'reject' ? 'destructive' : 'default'}
          disabled={sending}
          onClick={() => void confirm()}
        >
          {sending ? (
            <Spinner label={t(SENDING_KEYS[action])} showLabel />
          ) : (
            t(CONFIRM_KEYS[action])
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
