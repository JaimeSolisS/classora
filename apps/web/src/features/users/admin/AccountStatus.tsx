import { useId, useRef, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import type { StateAction, UserDetail } from './api';
import { isOpenInvitation } from './rules';

// The "Account status" section of the user detail page (#31 K3, K4, K6):
// Suspend, Archive and Reactivate, each shown only when the server's
// `allowed_actions` flag is true, each behind a confirmation dialog. The
// page's `onChange` calls the route through apiCall with the loaded version,
// so suspend and archive open the #28 password dialog and are retried once.
// Errors (and a cancelled password dialog) show in this section's alert; a
// version conflict adds "Reload". Without any API call: stories pass stubs.

const ACTIONS: readonly StateAction[] = ['suspend', 'archive', 'reactivate'];

interface AccountStatusProps {
  user: UserDetail;
  /** Sends the change; resolves once the cache holds the new detail. */
  onChange: (action: StateAction) => Promise<void>;
  onReload: () => Promise<void>;
}

export function AccountStatus({
  user,
  onChange,
  onReload,
}: AccountStatusProps) {
  const { t } = useTranslation();
  const id = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [pending, setPending] = useState<StateAction | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<StateAction | null>(null);

  async function confirm(action: StateAction) {
    setError(null);
    setDone(null);
    try {
      await onChange(action);
      setDone(action);
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(null);
    }
  }

  async function reload() {
    setError(null);
    await onReload();
  }

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const doneMessage =
    done === 'reactivate'
      ? user.password_set
        ? t('users.status.reactivated')
        : t('users.status.reactivatedNoPassword')
      : done === 'suspend'
        ? t('users.status.suspended')
        : done === 'archive'
          ? t('users.status.archived')
          : null;

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-4">
      <h2
        id={`${id}-title`}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {t('users.status.title')}
      </h2>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict ? () => void reload() : undefined}
          retryLabel={isConflict ? t('users.conflict.reload') : undefined}
        />
      ) : null}
      <div className="flex flex-wrap gap-3">
        {ACTIONS.filter((action) => user.allowed_actions[action]).map(
          (action) => (
            <Button
              key={action}
              type="button"
              variant={action === 'reactivate' ? 'default' : 'outline'}
              onClick={() => {
                setError(null);
                setDone(null);
                setPending(action);
              }}
            >
              {t(`users.status.${action}`)}
            </Button>
          ),
        )}
      </div>
      <p role="status" className="text-sm">
        {doneMessage}
      </p>
      <StateChangeDialog
        user={user}
        action={pending}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
        fallbackFocus={headingRef}
      />
    </section>
  );
}

interface StateChangeDialogProps {
  user: UserDetail;
  /** The action to confirm, or null when closed. */
  action: StateAction | null;
  onConfirm: (action: StateAction) => Promise<void>;
  onCancel: () => void;
  /** Gets focus on close when the button that opened the dialog is gone. */
  fallbackFocus?: RefObject<HTMLElement | null>;
}

/** The confirmation dialog of one state change (L1 copy). */
export function StateChangeDialog({
  user,
  action,
  onConfirm,
  onCancel,
  fallbackFocus,
}: StateChangeDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={action !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {action ? (
        <StateChangeDialogContent
          key={action}
          user={user}
          action={action}
          onConfirm={onConfirm}
          onCancel={onCancel}
          sendingRef={sending}
          fallbackFocus={fallbackFocus}
        />
      ) : null}
    </Dialog>
  );
}

function StateChangeDialogContent({
  user,
  action,
  onConfirm,
  onCancel,
  sendingRef,
  fallbackFocus,
}: {
  user: UserDetail;
  action: StateAction;
  onConfirm: (action: StateAction) => Promise<void>;
  onCancel: () => void;
  sendingRef: RefObject<boolean>;
  fallbackFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const [sending, setSending] = useState(false);
  // The element focused before the dialog opened (it opens without a
  // trigger), to give focus back on close.
  const returnFocus = useRef<Element | null>(null);
  const name = { name: user.display_name };
  const invitation = action === 'archive' && isOpenInvitation(user);
  const title =
    action === 'suspend'
      ? t('users.status.suspendTitle')
      : action === 'reactivate'
        ? t('users.status.reactivateTitle')
        : invitation
          ? t('users.status.archiveInvitationTitle')
          : t('users.status.archiveTitle');
  const description =
    action === 'suspend'
      ? t('users.status.suspendConfirm', name)
      : action === 'reactivate'
        ? t('users.status.reactivateConfirm', name)
        : invitation
          ? t('users.status.archiveInvitationConfirm', name)
          : t('users.status.archiveConfirm', name);

  async function confirm() {
    if (sendingRef.current) {
      return;
    }
    sendingRef.current = true;
    setSending(true);
    try {
      await onConfirm(action);
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
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={sending}
          onClick={onCancel}
        >
          {t('users.status.cancel')}
        </Button>
        <Button
          type="button"
          variant={action === 'reactivate' ? 'default' : 'destructive'}
          disabled={sending}
          onClick={() => void confirm()}
        >
          {sending ? (
            <Spinner label={t('users.status.working')} showLabel />
          ) : (
            t(`users.status.${action}`)
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
