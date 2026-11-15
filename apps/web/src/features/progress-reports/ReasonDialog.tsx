import { useId, useRef, useState, type ReactNode } from 'react';
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
import { Counter } from '@/features/academics/Counter';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  REASON_MAX,
  reasonError,
  reasonFieldError,
  type ReportMessage,
} from './rules';
import { useReportMessage } from './text';

// "Request changes" and "Retract" (#315 E2): what the decision does, an
// optional warning box, and a required Reason (trimmed 1-1000, with a
// counter). An empty or too long reason sends nothing and focus stays on
// the field; the server's reason errors land on it too. Any other refusal
// goes back to the page, which closes the dialog and shows it. The page
// moves focus once it closes (the trigger on Cancel or Escape).

export function ReasonDialog({
  open,
  title,
  description,
  warning,
  confirmLabel,
  busyLabel,
  destructive = false,
  onConfirm,
  onCancel,
  onFailure,
}: {
  open: boolean;
  title: string;
  description: string;
  warning?: ReactNode;
  confirmLabel: string;
  busyLabel: string;
  destructive?: boolean;
  /** Resolves when done; rejects with the error. */
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  /** A refusal that isn't about the reason. */
  onFailure: (error: unknown) => void;
}) {
  const { t } = useTranslation();
  const message = useReportMessage();
  const id = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<ReportMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const problemText = message(problem);

  function close() {
    setReason('');
    setProblem(null);
    onCancel();
  }

  async function confirm() {
    const found = reasonError(reason);
    setProblem(found);
    if (found) {
      field.current?.focus();
      return;
    }
    setBusy(true);
    try {
      await onConfirm(reason.trim());
      setReason('');
      setProblem(null);
    } catch (caught) {
      const onField = reasonFieldError(caught);
      if (onField) {
        setProblem(onField);
        // After the busy state ends, so the field can take focus.
        window.setTimeout(() => field.current?.focus(), 0);
      } else {
        setReason('');
        setProblem(null);
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
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {warning ? (
            <p className="rounded-md border border-border bg-muted p-3 text-sm font-medium">
              {warning}
            </p>
          ) : null}
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={`${id}-reason`} className="text-sm font-medium">
              {t('progressReports.review.reason')}
            </label>
            <textarea
              id={`${id}-reason`}
              ref={field}
              rows={4}
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
              {t('progressReports.cancel')}
            </Button>
            <Button
              type="button"
              variant={destructive ? 'destructive' : 'default'}
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? <Spinner label={busyLabel} showLabel /> : confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
