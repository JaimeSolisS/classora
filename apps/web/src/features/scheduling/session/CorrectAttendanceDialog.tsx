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
import { SELECT_CLASS } from '@/features/students/styles';
import {
  ATTENDANCE_STATUSES,
  type AttendanceItem,
  type AttendanceStatus,
} from '@/features/tutor-portal/attendance/api';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// "Correct attendance" (#76 G3), for holders of attendance.correct: the new
// status (the current one can't be chosen) and a required reason (1-1000
// characters once trimmed, with a counter). Without any API call: the
// section passes `onConfirm`, which resolves once the dialog may close and
// rejects with the error to show here. reason_required and
// attendance_unchanged go on their fields; version_conflict offers Reload.
// Focus starts on the status, is trapped while open and returns to the row's
// button.

export interface CorrectAttendanceDialogProps {
  /** The row being corrected; `null` closes the dialog. */
  item: AttendanceItem | null;
  onConfirm: (status: AttendanceStatus, reason: string) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories. */
  initialReason?: string;
  initialError?: unknown;
}

export function CorrectAttendanceDialog({
  item,
  onConfirm,
  onCancel,
  onReload,
  initialReason = '',
  initialError = null,
}: CorrectAttendanceDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {item ? (
        <CorrectContent
          key={item.student.id}
          item={item}
          onConfirm={onConfirm}
          onCancel={onCancel}
          onReload={onReload}
          initialReason={initialReason}
          initialError={initialError}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function firstOther(status: AttendanceStatus | null): AttendanceStatus {
  return ATTENDANCE_STATUSES.find((s) => s !== status) ?? 'present';
}

function CorrectContent({
  item,
  onConfirm,
  onCancel,
  onReload,
  initialReason,
  initialError,
  sendingRef,
}: Omit<CorrectAttendanceDialogProps, 'item'> & {
  item: AttendanceItem;
  initialReason: string;
  initialError: unknown;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const id = useId();
  const statusId = `${id}-status`;
  const statusErrorId = `${id}-status-error`;
  const reasonId = `${id}-reason`;
  const reasonErrorId = `${id}-reason-error`;
  const counterId = `${id}-reason-counter`;
  const select = useRef<HTMLSelectElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef<Element | null>(null);
  const [status, setStatus] = useState<AttendanceStatus>(
    firstOther(item.status),
  );
  const [reason, setReason] = useState(initialReason);
  const [statusError, setStatusError] = useState<string | undefined>();
  const [reasonError, setReasonError] = useState<string | undefined>();
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);
  const conflict =
    failure instanceof ApiError && failure.code === 'version_conflict';

  function check(): string | undefined {
    const length = reasonLength(reason);
    if (length === 0) {
      return t('attendance.correctDialog.reasonRequired');
    }
    if (length > REASON_MAX_LENGTH) {
      return t('attendance.correctDialog.reasonTooLong');
    }
    return undefined;
  }

  async function send() {
    setFailure(null);
    setStatusError(undefined);
    const problem = check();
    setReasonError(problem);
    if (problem) {
      textarea.current?.focus();
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm(status, reason.trim());
    } catch (caught) {
      const field =
        caught instanceof ApiError
          ? caught.fieldErrors.find(({ code }) =>
              [
                'reason_required',
                'reason_too_long',
                'attendance_unchanged',
              ].includes(code),
            )
          : undefined;
      if (field?.code === 'attendance_unchanged') {
        setStatusError(t('attendance.correctDialog.unchanged'));
        select.current?.focus();
      } else if (field) {
        setReasonError(
          field.code === 'reason_too_long'
            ? t('attendance.correctDialog.reasonTooLong')
            : t('attendance.correctDialog.reasonRequired'),
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
        select.current?.focus();
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
        <DialogTitle>{t('attendance.correctDialog.title')}</DialogTitle>
        <DialogDescription>
          {t('attendance.correctDialog.description', {
            name: item.student.display_name,
          })}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {conflict ? (
          <div
            role="alert"
            className="flex flex-col gap-2 rounded-md border border-destructive p-3 text-sm"
          >
            <p className="font-medium">{t('attendance.conflict')}</p>
            <div>
              <Button type="button" variant="outline" onClick={onReload}>
                {t('attendance.reload')}
              </Button>
            </div>
          </div>
        ) : failure ? (
          <ErrorAlert error={failure} />
        ) : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={statusId} className="text-sm font-medium">
            {t('attendance.correctDialog.status')}
          </label>
          <select
            id={statusId}
            ref={select}
            className={SELECT_CLASS}
            value={status}
            disabled={busy}
            aria-invalid={statusError ? true : undefined}
            aria-describedby={statusError ? statusErrorId : undefined}
            onChange={(event) =>
              setStatus(event.target.value as AttendanceStatus)
            }
          >
            {ATTENDANCE_STATUSES.map((option) => (
              <option
                key={option}
                value={option}
                disabled={option === item.status}
              >
                {option === item.status
                  ? t('attendance.correctDialog.current', {
                      status: t(`attendance.statuses.${option}`),
                    })
                  : t(`attendance.statuses.${option}`)}
              </option>
            ))}
          </select>
          <FieldError id={statusErrorId} message={statusError} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('attendance.correctDialog.reason')}
          </label>
          <textarea
            id={reasonId}
            ref={textarea}
            rows={4}
            required
            value={reason}
            aria-invalid={reasonError ? true : undefined}
            aria-describedby={[
              reasonError ? reasonErrorId : undefined,
              counterId,
            ]
              .filter(Boolean)
              .join(' ')}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
          />
          <FieldError id={reasonErrorId} message={reasonError} />
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
            {t('attendance.correctDialog.cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner
                label={t('attendance.correctDialog.submitting')}
                showLabel
              />
            ) : (
              t('attendance.correctDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
