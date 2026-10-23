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
import { DECISION_REASON_MAX_LENGTH, decisionReasonProblem } from './rules';

// The reason of a decision on a change request (#62 G1): "Approve as
// exception" ("Treated as excused; the family will see this reason.") or
// "Reject" ("The requester will see this reason."). A required reason,
// 1-1000 characters once trimmed, with a counter. Without any API call: the
// page passes `onConfirm`, which resolves once the dialog may close and
// rejects with the error to show here (a 422 on the reason goes on the
// field). The focus starts on the reason (never on the destructive button),
// is trapped while open and returns to the button that opened the dialog
// (once the request is decided and that button is gone, the page moves it
// to the outcome). No autoFocus: the focus is moved by onOpenAutoFocus.

export type DecisionReasonMode = 'exception' | 'reject';

export interface DecisionReasonDialogProps {
  open: boolean;
  mode: DecisionReasonMode;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  /**
   * Where the focus goes when the dialog closes, instead of the button that
   * opened it (a conflict panel shown by the approval); null: that button.
   */
  closeFocusTarget?: () => HTMLElement | null;
  /** Stories. */
  initialReason?: string;
  initialError?: unknown;
  initialProblem?: 'required' | 'tooLong';
}

export function DecisionReasonDialog({
  open,
  mode,
  onConfirm,
  onCancel,
  closeFocusTarget,
  initialReason = '',
  initialError = null,
  initialProblem,
}: DecisionReasonDialogProps) {
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
        <ReasonContent
          mode={mode}
          onConfirm={onConfirm}
          onCancel={onCancel}
          closeFocusTarget={closeFocusTarget}
          initialReason={initialReason}
          initialError={initialError}
          initialProblem={initialProblem}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function ReasonContent({
  mode,
  onConfirm,
  onCancel,
  closeFocusTarget,
  initialReason,
  initialError,
  initialProblem,
  sendingRef,
}: Omit<DecisionReasonDialogProps, 'open'> & {
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
  const [problem, setProblem] = useState<'required' | 'tooLong' | undefined>(
    initialProblem,
  );
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);
  const texts =
    mode === 'exception'
      ? {
          title: t('scheduling.changeRequest.exception.title'),
          description: t('scheduling.changeRequest.exception.description'),
          keep: t('scheduling.changeRequest.exception.keep'),
          submit: t('scheduling.changeRequest.exception.submit'),
          submitting: t('scheduling.changeRequest.exception.submitting'),
        }
      : {
          title: t('scheduling.changeRequest.reject.title'),
          description: t('scheduling.changeRequest.reject.description'),
          keep: t('scheduling.changeRequest.reject.keep'),
          submit: t('scheduling.changeRequest.reject.submit'),
          submitting: t('scheduling.changeRequest.reject.submitting'),
        };

  async function send() {
    setFailure(null);
    const found = decisionReasonProblem(reason) ?? undefined;
    setProblem(found);
    if (found) {
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
        setProblem(
          onReason.code === 'string_too_long' ? 'tooLong' : 'required',
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

  const length = reason.trim().length;
  const problemText =
    problem === 'tooLong'
      ? t('scheduling.changeRequest.reasonTooLong')
      : problem === 'required'
        ? t('scheduling.changeRequest.reasonRequired')
        : undefined;

  return (
    <DialogContent
      onOpenAutoFocus={(event) => {
        returnFocus.current = document.activeElement;
        event.preventDefault();
        textarea.current?.focus();
      }}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        const target = closeFocusTarget?.() ?? returnFocus.current;
        if (target instanceof HTMLElement && target.isConnected) {
          target.focus();
        }
      }}
      onInteractOutside={(event) => event.preventDefault()}
      className="sm:max-w-lg"
    >
      <DialogHeader>
        <DialogTitle>{texts.title}</DialogTitle>
        <DialogDescription>{texts.description}</DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? <ErrorAlert error={failure} /> : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('scheduling.changeRequest.reason')}
          </label>
          <textarea
            id={reasonId}
            ref={textarea}
            rows={4}
            required
            value={reason}
            aria-invalid={problemText ? true : undefined}
            aria-describedby={[problemText ? errorId : undefined, counterId]
              .filter(Boolean)
              .join(' ')}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
          />
          <FieldError id={errorId} message={problemText} />
          <p
            id={counterId}
            className={
              length > DECISION_REASON_MAX_LENGTH
                ? 'text-sm text-destructive'
                : 'text-sm text-muted-foreground'
            }
          >
            {t('scheduling.substitution.counter', {
              length,
              max: DECISION_REASON_MAX_LENGTH,
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
            {texts.keep}
          </Button>
          <Button
            type="submit"
            variant={mode === 'reject' ? 'destructive' : 'default'}
            disabled={busy}
          >
            {busy ? (
              <Spinner label={texts.submitting} showLabel />
            ) : (
              texts.submit
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
