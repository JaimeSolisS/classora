import { useId, useRef, useState, type FormEvent } from 'react';
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
import { FieldError } from '@/features/auth/FieldError';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { toLanguage } from '@/i18n/language';
import { NOTE_MAX_LENGTH, type AvailableSlot } from './api';
import { noteLength, noteTooLong, retryMinutes, slotTimes } from './rules';

// "Send request" (#59 F2): the summary of the chosen time, an optional note
// for the center (at most 500 characters once trimmed, with a counter
// announced politely) and the reminder that a request is not a booking.
// Without any API call: the page passes `onConfirm`, which resolves once the
// request was sent and rejects with the error to show here (F3: a `429`
// says how many minutes to wait; any other code gets its catalog message).
// Focus returns to the slot that opened it (useReturnFocus), unless the page
// moved it to the success notice.

export interface RequestSummary {
  studentName: string;
  subjectName: string;
  duration: number;
}

export interface ConfirmRequestDialogProps {
  /** The chosen slot; null: closed. */
  slot: AvailableSlot | null;
  summary: RequestSummary;
  timeZone: string;
  onConfirm: (note: string) => Promise<void>;
  onCancel: () => void;
  /** Stories. */
  initialNote?: string;
  initialFailure?: unknown;
}

export function ConfirmRequestDialog(props: ConfirmRequestDialogProps) {
  const busy = useRef(false);
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  const { slot } = props;
  return (
    <Dialog
      open={slot !== null}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          props.onCancel();
        }
      }}
    >
      {slot ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{t('sessionRequests.confirm.title')}</DialogTitle>
            <DialogDescription>
              {t('sessionRequests.confirm.notBooking')}
            </DialogDescription>
          </DialogHeader>
          <ConfirmContent
            {...props}
            slot={slot}
            key={slot.starts_at}
            busyRef={busy}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function ConfirmContent({
  slot,
  summary,
  timeZone,
  onConfirm,
  onCancel,
  initialNote = '',
  initialFailure = null,
  busyRef,
}: ConfirmRequestDialogProps & {
  slot: AvailableSlot;
  busyRef: { current: boolean };
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const noteId = `${id}-note`;
  const errorId = `${id}-note-error`;
  const counterId = `${id}-note-counter`;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [note, setNote] = useState(initialNote);
  const [noteError, setNoteError] = useState<string | undefined>(
    noteTooLong(initialNote)
      ? t('sessionRequests.confirm.noteTooLong')
      : undefined,
  );
  const [failure, setFailure] = useState<unknown>(initialFailure);
  const [busy, setBusy] = useState(false);
  const length = noteLength(note);
  const times = slotTimes(slot, timeZone, language);
  const minutes = failure ? retryMinutes(failure) : null;

  async function send() {
    setFailure(null);
    if (noteTooLong(note)) {
      setNoteError(t('sessionRequests.confirm.noteTooLong'));
      textarea.current?.focus();
      return;
    }
    setNoteError(undefined);
    busyRef.current = true;
    setBusy(true);
    try {
      await onConfirm(note);
    } catch (caught) {
      setFailure(caught);
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
      <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="font-medium">{t('sessionRequests.confirm.student')}</dt>
        <dd className="min-w-0 break-words">{summary.studentName}</dd>
        <dt className="font-medium">{t('sessionRequests.confirm.subject')}</dt>
        <dd className="min-w-0 break-words">{summary.subjectName}</dd>
        <dt className="font-medium">{t('sessionRequests.confirm.when')}</dt>
        <dd className="min-w-0 break-words">
          {t('sessionRequests.when', {
            date: times.date,
            start: times.start,
            end: times.end,
          })}
        </dd>
        <dt className="font-medium">{t('sessionRequests.confirm.length')}</dt>
        <dd>{t('sessionRequests.minutes', { minutes: summary.duration })}</dd>
      </dl>
      {failure ? (
        minutes !== null ? (
          <ErrorAlert
            error={failure}
            message={t('sessionRequests.tooMany', { count: minutes })}
          />
        ) : (
          <ErrorAlert error={failure} />
        )
      ) : null}
      <div className="flex flex-col gap-2">
        <label htmlFor={noteId} className="text-sm font-medium">
          {t('sessionRequests.confirm.note')}
        </label>
        <textarea
          id={noteId}
          ref={textarea}
          rows={3}
          value={note}
          aria-invalid={noteError ? true : undefined}
          aria-describedby={[noteError ? errorId : undefined, counterId]
            .filter(Boolean)
            .join(' ')}
          disabled={busy}
          onChange={(event) => setNote(event.target.value)}
          className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
        />
        <FieldError id={errorId} message={noteError} />
        <p
          id={counterId}
          aria-live="polite"
          className={
            length > NOTE_MAX_LENGTH
              ? 'text-sm text-destructive'
              : 'text-sm text-muted-foreground'
          }
        >
          {t('sessionRequests.confirm.counter', {
            length,
            max: NOTE_MAX_LENGTH,
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
          {t('sessionRequests.confirm.cancel')}
        </Button>
        <Button type="submit" disabled={busy}>
          {busy ? (
            <Spinner label={t('sessionRequests.confirm.sending')} showLabel />
          ) : (
            t('sessionRequests.confirm.send')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
