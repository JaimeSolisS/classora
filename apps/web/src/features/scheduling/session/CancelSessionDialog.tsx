import { useId, useRef, useState, type FormEvent } from 'react';
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
import { FieldError } from '@/features/auth/FieldError';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// "Cancel session" (#55 H1), for holders of schedule.manage: a required
// reason (1-1000 characters once trimmed, with a counter) and what happens
// (the tutor and the family are notified, pending offers are withdrawn).
// Without any API call: the page passes `onConfirm`, which resolves once the
// dialog may close and rejects with the error to show here (a 422 on the
// reason goes on the field). Focus starts on the reason, never on the
// destructive button, is trapped while open and returns to the button that
// opened the dialog.

export interface CancelSessionDialogProps {
  open: boolean;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  /** Stories. */
  initialReason?: string;
  initialError?: unknown;
}

export function CancelSessionDialog({
  open,
  onConfirm,
  onCancel,
  initialReason = '',
  initialError = null,
}: CancelSessionDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {open ? (
        <CancelContent
          onConfirm={onConfirm}
          onCancel={onCancel}
          initialReason={initialReason}
          initialError={initialError}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function CancelContent({
  onConfirm,
  onCancel,
  initialReason,
  initialError,
  sendingRef,
}: Omit<CancelSessionDialogProps, 'open'> & {
  initialReason: string;
  initialError: unknown;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const id = useId();
  const reasonId = `${id}-reason`;
  const errorId = `${id}-reason-error`;
  const counterId = `${id}-reason-counter`;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef<Element | null>(null);
  const [reason, setReason] = useState(initialReason);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);

  function check(): string | undefined {
    const length = reasonLength(reason);
    if (length === 0) {
      return t('scheduling.cancel.validation.reasonRequired');
    }
    if (length > REASON_MAX_LENGTH) {
      return t('scheduling.cancel.validation.reasonTooLong');
    }
    return undefined;
  }

  async function send() {
    setFailure(null);
    const problem = check();
    setReasonError(problem);
    if (problem) {
      textarea.current?.focus();
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm(reason.trim());
    } catch (caught) {
      const onReason =
        caught instanceof ApiError &&
        caught.fieldErrors.find(
          ({ path }) => path[path.length - 1] === 'reason',
        );
      if (onReason) {
        setReasonError(
          onReason.code === 'reason_too_long'
            ? t('scheduling.cancel.validation.reasonTooLong')
            : t('scheduling.cancel.validation.reasonRequired'),
        );
        textarea.current?.focus();
      } else {
        setFailure(caught);
      }
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sendingRef.current) {
      void send();
    }
  }

  const length = reasonLength(reason);

  return (
    <DialogContent
      onOpenAutoFocus={(event) => {
        returnFocus.current = document.activeElement;
        event.preventDefault();
        textarea.current?.focus();
      }}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        const target = returnFocus.current;
        if (target instanceof HTMLElement && target.isConnected) {
          target.focus();
        }
      }}
      onInteractOutside={(event) => event.preventDefault()}
      className="sm:max-w-lg"
    >
      <DialogHeader>
        <DialogTitle>{t('scheduling.cancel.title')}</DialogTitle>
        <DialogDescription>
          {t('scheduling.cancel.description')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? <ErrorAlert error={failure} /> : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('scheduling.cancel.reason')}
          </label>
          <textarea
            id={reasonId}
            ref={textarea}
            rows={4}
            required
            value={reason}
            aria-invalid={reasonError ? true : undefined}
            aria-describedby={[reasonError ? errorId : undefined, counterId]
              .filter(Boolean)
              .join(' ')}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
          />
          <FieldError id={errorId} message={reasonError} />
          <p
            id={counterId}
            className={
              length > REASON_MAX_LENGTH
                ? 'text-sm text-destructive'
                : 'text-sm text-muted-foreground'
            }
          >
            {t('scheduling.substitution.counter', {
              length,
              max: REASON_MAX_LENGTH,
            })}
          </p>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onCancel}
          >
            {t('scheduling.cancel.keep')}
          </Button>
          <Button type="submit" variant="destructive" disabled={busy}>
            {busy ? (
              <Spinner label={t('scheduling.cancel.submitting')} showLabel />
            ) : (
              t('scheduling.cancel.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
