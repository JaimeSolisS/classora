import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import { reasonError } from './closeoutReview';
import { REVIEW_TEXTAREA_CLASS, ReviewFailure } from './ReviewFailure';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// "Request changes" (#78 G1): a required reason (1-1000 characters once
// trimmed, with a counter) that the tutor reads. Nothing is sent while the
// reason is empty or too long; the server's reason errors go on the field.
// Focus starts on the reason and returns to the trigger.

export interface RequestChangesDialogProps {
  open: boolean;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories. */
  initialReason?: string;
  initialError?: unknown;
}

export function RequestChangesDialog(props: RequestChangesDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          props.onCancel();
        }
      }}
    >
      {props.open ? <RequestContent {...props} sendingRef={sending} /> : null}
    </Dialog>
  );
}

function RequestContent({
  onConfirm,
  onCancel,
  onReload,
  initialReason = '',
  initialError = null,
  sendingRef,
}: RequestChangesDialogProps & { sendingRef: { current: boolean } }) {
  const { t } = useTranslation();
  const id = useId();
  const reasonId = `${id}-reason`;
  const errorId = `${id}-reason-error`;
  const counterId = `${id}-reason-counter`;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef<Element | null>(null);
  const [reason, setReason] = useState(initialReason);
  const [problem, setProblem] = useState<string | undefined>();
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);

  function message(code: string): string {
    return code === 'reason_too_long'
      ? t('closeout.review.reasonTooLong')
      : t('closeout.review.reasonRequired');
  }

  async function send() {
    setFailure(null);
    const code = reasonError(reason);
    setProblem(code ? message(code) : undefined);
    if (code) {
      textarea.current?.focus();
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm(reason.trim());
    } catch (caught) {
      const field =
        caught instanceof ApiError
          ? caught.fieldErrors.find(({ code: c }) =>
              ['reason_required', 'reason_too_long'].includes(c),
            )
          : undefined;
      if (field) {
        setProblem(message(field.code));
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
        <DialogTitle>{t('closeout.review.requestDialog.title')}</DialogTitle>
        <DialogDescription>
          {t('closeout.review.requestDialog.description')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <ReviewFailure error={failure} onReload={onReload} />
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('closeout.review.requestDialog.reason')}{' '}
            <span className="font-normal text-muted-foreground">
              {t('closeout.required')}
            </span>
          </label>
          <textarea
            id={reasonId}
            ref={textarea}
            rows={4}
            required
            aria-required
            value={reason}
            aria-invalid={problem ? true : undefined}
            aria-describedby={[problem ? errorId : undefined, counterId]
              .filter(Boolean)
              .join(' ')}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            className={REVIEW_TEXTAREA_CLASS}
          />
          <FieldError id={errorId} message={problem} />
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
            {t('closeout.review.cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner
                label={t('closeout.review.requestDialog.submitting')}
                showLabel
              />
            ) : (
              t('closeout.review.requestDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
