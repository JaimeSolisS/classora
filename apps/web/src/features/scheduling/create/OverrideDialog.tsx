import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
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
import type { SchedulingConflict } from './api';
import { useConflictMessage } from './conflictMessages';
import {
  OVERRIDE_REASON_MAX_LENGTH,
  overrideReasonError,
  validateOverrideReason,
  type ReasonMessage,
} from './rules';

// "Override and schedule" (#49 F3, F4): lists every soft conflict being
// overridden (all of them together, read-only) and asks for a required
// reason (1-1000 characters once trimmed, with a counter). Without any API
// call: the form passes `onConfirm`, which resolves once the dialog may
// close (created, or a new conflict list) and rejects with the error to show
// here: a 422 on the reason goes on the field, anything else (a 403, for
// example) in the shared ErrorAlert. The dialog traps the focus and closes
// on Esc; `onCloseAutoFocus` lets the form choose where the focus goes.

export interface OverrideDialogProps {
  open: boolean;
  /** The soft conflicts to override (the panel's current list). */
  conflicts: readonly SchedulingConflict[];
  /** Resends the create with the trimmed reason. */
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  onOpenAutoFocus?: (event: Event) => void;
  onCloseAutoFocus?: (event: Event) => void;
  /**
   * A failure that needs fresh data (#236: a version conflict on a series
   * row) shows a "Reload" button calling this.
   */
  reload?: { when: (error: unknown) => boolean; onReload: () => void };
  /** The reason to start from (stories). */
  initialReason?: string;
  /**
   * Translated texts for another write than the create (#52: "Override and
   * offer"); default: the create's.
   */
  labels?: OverrideDialogLabels;
  /**
   * The read-only list of what is overridden, in place of the flat list of
   * `conflicts` (#236: a series groups them by local date).
   */
  rules?: ReactNode;
}

export interface OverrideDialogLabels {
  title: string;
  description: string;
  submit: string;
  submitting: string;
}

export function OverrideDialog({
  open,
  conflicts,
  onConfirm,
  onCancel,
  onOpenAutoFocus,
  onCloseAutoFocus,
  reload,
  initialReason = '',
  labels,
  rules,
}: OverrideDialogProps) {
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
        <OverrideContent
          conflicts={conflicts}
          onConfirm={onConfirm}
          onCancel={onCancel}
          onOpenAutoFocus={onOpenAutoFocus}
          onCloseAutoFocus={onCloseAutoFocus}
          reload={reload}
          initialReason={initialReason}
          sendingRef={sending}
          labels={labels}
          rules={rules}
        />
      ) : null}
    </Dialog>
  );
}

function OverrideContent({
  conflicts,
  onConfirm,
  onCancel,
  onOpenAutoFocus,
  onCloseAutoFocus,
  reload,
  initialReason,
  sendingRef,
  labels,
  rules,
}: Omit<OverrideDialogProps, 'open'> & {
  initialReason: string;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const message = useConflictMessage();
  const id = useId();
  const reasonId = `${id}-reason`;
  const errorId = `${id}-reason-error`;
  const counterId = `${id}-reason-counter`;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState(initialReason);
  const [reasonError, setReasonError] = useState<
    ReasonMessage | 'errors:validation_error' | undefined
  >(undefined);
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    const found = validateOverrideReason(reason);
    setReasonError(found);
    setFailure(null);
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
        caught instanceof ApiError
          ? overrideReasonError(caught.fieldErrors)
          : null;
      if (onReason) {
        setReasonError(onReason);
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

  const errorMessage = reasonError
    ? reasonError === 'errors:validation_error'
      ? tErrors('validation_error')
      : t(reasonError)
    : undefined;
  const length = reason.trim().length;

  return (
    <DialogContent
      onOpenAutoFocus={onOpenAutoFocus}
      onCloseAutoFocus={onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
    >
      <DialogHeader>
        <DialogTitle>
          {labels?.title ?? t('scheduling.override.title')}
        </DialogTitle>
        <DialogDescription>
          {labels?.description ?? t('scheduling.override.description')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? (
          reload?.when(failure) ? (
            <ErrorAlert
              error={failure}
              onRetry={reload.onReload}
              retryLabel={t('scheduling.series.reload')}
            />
          ) : (
            <ErrorAlert error={failure} />
          )
        ) : null}
        <section
          aria-labelledby={`${id}-rules`}
          className="flex flex-col gap-2"
        >
          <h3 id={`${id}-rules`} className="font-medium">
            {t('scheduling.override.rules')}
          </h3>
          {rules ?? (
            <ul className="flex list-disc flex-col gap-1 pl-5">
              {conflicts.map((conflict, index) => (
                <li key={`${conflict.code}-${index}`} className="break-words">
                  {message(conflict)}
                </li>
              ))}
            </ul>
          )}
        </section>
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('scheduling.override.reason')}
          </label>
          <textarea
            id={reasonId}
            ref={textarea}
            rows={4}
            required
            value={reason}
            aria-invalid={errorMessage ? true : undefined}
            aria-describedby={[errorMessage ? errorId : undefined, counterId]
              .filter(Boolean)
              .join(' ')}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
          />
          <FieldError id={errorId} message={errorMessage} />
          <p
            id={counterId}
            className={
              length > OVERRIDE_REASON_MAX_LENGTH
                ? 'text-sm text-destructive'
                : 'text-sm text-muted-foreground'
            }
          >
            {t('scheduling.override.counter', {
              length,
              max: OVERRIDE_REASON_MAX_LENGTH,
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
            {t('scheduling.override.cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner
                label={
                  labels?.submitting ?? t('scheduling.override.submitting')
                }
                showLabel
              />
            ) : (
              (labels?.submit ?? t('scheduling.override.submit'))
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
