import { useRef, useState, type RefObject } from 'react';
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
import { roleNameKey } from '../roles';
import type { Decision, RegistrationRequestItem } from './api';

// The confirmation for approving or rejecting a request (#169 G3), naming
// the person and the requested role. Without any API call: the page passes
// `onConfirm`, stories pass a stub. While the decision is being sent the
// confirm button is disabled and shows the Spinner, and nothing cancels.

export interface PendingDecision {
  item: RegistrationRequestItem;
  decision: Decision;
}

interface DecisionDialogProps {
  /** The decision to confirm, or null when closed. */
  pending: PendingDecision | null;
  /** Sends the decision. Rejects with the error to show in the dialog. */
  onConfirm: (pending: PendingDecision) => Promise<void>;
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
          key={`${pending.item.id}-${pending.decision}`}
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

function DecisionDialogContent({
  pending,
  onConfirm,
  onCancel,
  sendingRef,
  fallbackFocus,
}: {
  pending: PendingDecision;
  onConfirm: (pending: PendingDecision) => Promise<void>;
  onCancel: () => void;
  sendingRef: RefObject<boolean>;
  fallbackFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const [error, setError] = useState<unknown>(null);
  const [sending, setSending] = useState(false);
  // The element focused before the dialog opened (it opens without a
  // trigger), to give focus back on close.
  const returnFocus = useRef<Element | null>(null);
  const { item, decision } = pending;
  const names = {
    name: item.display_name,
    role: t(roleNameKey(item.requested_role)),
  };
  const approve = decision === 'approve';

  async function confirm() {
    if (sendingRef.current) {
      return;
    }
    sendingRef.current = true;
    setSending(true);
    setError(null);
    try {
      await onConfirm(pending);
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
        <DialogTitle>
          {approve
            ? t('registrations.approveTitle', names)
            : t('registrations.rejectTitle', names)}
        </DialogTitle>
        <DialogDescription>
          {approve
            ? t('registrations.approveConfirm', names)
            : t('registrations.rejectConfirm', { username: item.username })}
        </DialogDescription>
      </DialogHeader>
      {error ? <ErrorAlert error={error} /> : null}
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={sending}
          onClick={onCancel}
        >
          {t('registrations.cancel')}
        </Button>
        <Button
          type="button"
          variant={approve ? 'default' : 'destructive'}
          disabled={sending}
          onClick={() => void confirm()}
        >
          {sending ? (
            <Spinner
              label={
                approve
                  ? t('registrations.approving')
                  : t('registrations.rejecting')
              }
              showLabel
            />
          ) : approve ? (
            t('registrations.approve')
          ) : (
            t('registrations.reject')
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
