import { useId, useRef, useState, type FormEvent } from 'react';
import type { TFunction } from 'i18next';
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
import { DialogFailure } from '@/features/scheduling/series/dialogParts';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import type { GroupEnrollmentItem } from './api';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// "Withdraw" (#234 D3): a confirmation that says what happens, and an
// optional reason (at most 1000 characters once trimmed, with a counter
// announced politely; whitespace only is no reason). Without any API call:
// the page passes `onConfirm`, which resolves once the dialog may close and
// rejects with the error to show here. A version conflict says "This
// changed since you opened it" with a reload; enrollment_not_active is the
// errors catalog's message (the page refreshes the roster); a 422 on the
// reason goes on the field.
//
// A cohort withdrawal (#245 C1) reuses it with its own texts (`labels`,
// including a help line on what happens to the sessions).

/** Texts for another withdrawal than a group's (#245). */
export interface WithdrawDialogLabels {
  title: string;
  /** The question, with the student's name. */
  description: (name: string) => string;
  /** A line under it (what happens to the sessions). */
  help?: string;
  reason: string;
  reasonTooLong: string;
  keep: string;
  submit: string;
  submitting: string;
}

export interface WithdrawDialogProps {
  /** The enrollment to withdraw; null: closed. */
  enrollment: GroupEnrollmentItem | null;
  groupName: string;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
  /** Reads the group again (after a version conflict). */
  onReload: () => void;
  /** Texts for another withdrawal (#245); default: the group's. */
  labels?: WithdrawDialogLabels;
  /** Stories. */
  initialReason?: string;
  initialFailure?: unknown;
}

export function WithdrawDialog(props: WithdrawDialogProps) {
  const busy = useRef(false);
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  const { enrollment } = props;
  const labels = withdrawLabels(t, props.groupName, props.labels);
  return (
    <Dialog
      open={enrollment !== null}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          props.onCancel();
        }
      }}
    >
      {enrollment ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{labels.title}</DialogTitle>
            <DialogDescription>
              {labels.description(enrollment.student.display_name)}
            </DialogDescription>
            {labels.help ? <p className="text-sm">{labels.help}</p> : null}
          </DialogHeader>
          <WithdrawContent
            {...props}
            key={`${enrollment.id}-${enrollment.version}`}
            busyRef={busy}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function withdrawLabels(
  t: TFunction,
  groupName: string,
  custom: WithdrawDialogLabels | undefined,
): WithdrawDialogLabels {
  return (
    custom ?? {
      title: t('groups.withdraw.title'),
      description: (name) =>
        t('groups.withdraw.description', { name, group: groupName }),
      reason: t('groups.withdraw.reason'),
      reasonTooLong: t('groups.withdraw.reasonTooLong'),
      keep: t('groups.withdraw.keep'),
      submit: t('groups.withdraw.submit'),
      submitting: t('groups.withdraw.submitting'),
    }
  );
}

function WithdrawContent({
  groupName,
  labels: custom,
  onConfirm,
  onCancel,
  onReload,
  initialReason = '',
  initialFailure = null,
  busyRef,
}: WithdrawDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const labels = withdrawLabels(t, groupName, custom);
  const id = useId();
  const reasonId = `${id}-reason`;
  const errorId = `${id}-reason-error`;
  const counterId = `${id}-reason-counter`;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState(initialReason);
  const [reasonError, setReasonError] = useState<string | undefined>(
    reasonLength(initialReason) > REASON_MAX_LENGTH
      ? labels.reasonTooLong
      : undefined,
  );
  const [failure, setFailure] = useState<unknown>(initialFailure);
  const [busy, setBusy] = useState(false);
  const length = reasonLength(reason);

  async function send() {
    setFailure(null);
    if (length > REASON_MAX_LENGTH) {
      setReasonError(labels.reasonTooLong);
      textarea.current?.focus();
      return;
    }
    setReasonError(undefined);
    busyRef.current = true;
    setBusy(true);
    try {
      await onConfirm(reason);
    } catch (caught) {
      const onReason =
        caught instanceof ApiError &&
        caught.fieldErrors.some(
          ({ path }) => path[path.length - 1] === 'reason',
        );
      if (onReason) {
        setReasonError(labels.reasonTooLong);
        textarea.current?.focus();
      } else {
        setFailure(caught);
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void send();
    }
  }

  return (
    <form
      noValidate
      onSubmit={onSubmit}
      className="flex min-w-0 flex-col gap-4"
    >
      {failure ? <DialogFailure error={failure} onReload={onReload} /> : null}
      <div className="flex flex-col gap-2">
        <label htmlFor={reasonId} className="text-sm font-medium">
          {labels.reason}
        </label>
        <textarea
          id={reasonId}
          ref={textarea}
          rows={4}
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
          aria-live="polite"
          className={
            length > REASON_MAX_LENGTH
              ? 'text-sm text-destructive'
              : 'text-sm text-muted-foreground'
          }
        >
          {t('groups.counter', { length, max: REASON_MAX_LENGTH })}
        </p>
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={onCancel}
        >
          {labels.keep}
        </Button>
        <Button type="submit" variant="destructive" disabled={busy}>
          {busy ? (
            <Spinner label={labels.submitting} showLabel />
          ) : (
            labels.submit
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
