import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
import { Counter } from '../Counter';
import { TEXTAREA_CLASS } from '../styles';
import { useEvaluationMessage } from './useEvaluationMessage';
import {
  REASON_MAX,
  serverVoidReasonError,
  voidReasonError,
  type EvaluationMessage,
} from './rules';

// "Void this evaluation" (#308 C7, staff with can_void): what voiding does
// and a required Reason (trimmed 1-500, with a counter). An empty reason
// sends nothing and focus stays on the field; the server's
// `reason_required` lands on it too. Confirm sends POST …/void with the
// version and the reason; any other refusal goes back to the page, which
// closes the dialog and shows it. The page moves focus once it closes.

export function VoidDialog({
  open,
  onConfirm,
  onCancel,
  onFailure,
}: {
  open: boolean;
  /** Resolves when done; rejects with the error. */
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  /** A refusal that isn't about the reason. */
  onFailure: (error: unknown) => void;
}) {
  const { t } = useTranslation();
  const message = useEvaluationMessage();
  const id = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<EvaluationMessage | undefined>();
  const [busy, setBusy] = useState(false);
  const problemText = message(problem);

  function close() {
    setReason('');
    setProblem(undefined);
    onCancel();
  }

  async function confirm() {
    const found = voidReasonError(reason);
    setProblem(found);
    if (found) {
      field.current?.focus();
      return;
    }
    setBusy(true);
    try {
      await onConfirm(reason.trim());
      setReason('');
      setProblem(undefined);
    } catch (caught) {
      const onField = serverVoidReasonError(caught);
      if (onField) {
        setProblem(onField);
        // After the busy state ends, so the field can take focus.
        window.setTimeout(() => field.current?.focus(), 0);
      } else {
        setReason('');
        onFailure(caught);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) {
          close();
        }
      }}
    >
      {open ? (
        <DialogContent
          className="sm:max-w-lg"
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{t('academics.evaluations.void.title')}</DialogTitle>
            <DialogDescription>
              {t('academics.evaluations.void.description')}
            </DialogDescription>
          </DialogHeader>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={`${id}-reason`} className="text-sm font-medium">
              {t('academics.evaluations.void.reason')}
            </label>
            <textarea
              id={`${id}-reason`}
              ref={field}
              rows={3}
              className={TEXTAREA_CLASS}
              value={reason}
              aria-invalid={problemText ? true : undefined}
              aria-describedby={[
                problemText ? `${id}-error` : null,
                `${id}-counter`,
              ]
                .filter(Boolean)
                .join(' ')}
              onChange={(event) => setReason(event.target.value)}
            />
            <FieldError id={`${id}-error`} message={problemText} />
            <Counter id={`${id}-counter`} value={reason} max={REASON_MAX} />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={close}
            >
              {t('academics.evaluations.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? (
                <Spinner
                  label={t('academics.evaluations.void.busy')}
                  showLabel
                />
              ) : (
                t('academics.evaluations.void.confirm')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
