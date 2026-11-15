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
import { DECLINE_NOTE_MAX_LENGTH, noteLength, noteTooLong } from './rules';

// "Decline the offer" (#52 H1): an optional note for the staff (at most 500
// characters once trimmed, with a counter). Without any API call: the page
// passes `onConfirm`, which resolves once the dialog may close and rejects
// with the error to show here (a 422 on the note goes on the field). Focus
// is trapped while open and returns to the card's Decline button.

export interface DeclineDialogProps {
  open: boolean;
  /** What is being declined ("Matemáticas, 20 oct 2026, 10:00"). */
  offerLabel: string;
  onConfirm: (note: string) => Promise<void>;
  onCancel: () => void;
  /** The note to start from (stories). */
  initialNote?: string;
  /** An error to start with (stories). */
  initialError?: unknown;
}

export function DeclineDialog({
  open,
  offerLabel,
  onConfirm,
  onCancel,
  initialNote = '',
  initialError = null,
}: DeclineDialogProps) {
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
        <DeclineContent
          offerLabel={offerLabel}
          onConfirm={onConfirm}
          onCancel={onCancel}
          initialNote={initialNote}
          initialError={initialError}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function DeclineContent({
  offerLabel,
  onConfirm,
  onCancel,
  initialNote,
  initialError,
  sendingRef,
}: Omit<DeclineDialogProps, 'open'> & {
  initialNote: string;
  initialError: unknown;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const noteId = `${id}-note`;
  const errorId = `${id}-note-error`;
  const counterId = `${id}-note-counter`;
  const textarea = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef<Element | null>(null);
  const [note, setNote] = useState(initialNote);
  const [noteError, setNoteError] = useState<string | undefined>(undefined);
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);

  async function send() {
    setFailure(null);
    if (noteTooLong(note)) {
      setNoteError(tErrors('decline_note_too_long'));
      textarea.current?.focus();
      return;
    }
    setNoteError(undefined);
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm(note.trim());
    } catch (caught) {
      const onNote =
        caught instanceof ApiError &&
        caught.fieldErrors.some(({ path }) => path[path.length - 1] === 'note');
      if (onNote) {
        setNoteError(tErrors('decline_note_too_long'));
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

  const length = noteLength(note);

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
        }
      }}
      onInteractOutside={(event) => event.preventDefault()}
      className="sm:max-w-lg"
    >
      <DialogHeader>
        <DialogTitle>{t('offers.decline.title')}</DialogTitle>
        <DialogDescription>
          {t('offers.decline.description', { offer: offerLabel })}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? <ErrorAlert error={failure} /> : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={noteId} className="text-sm font-medium">
            {t('offers.decline.note')}
          </label>
          <textarea
            id={noteId}
            ref={textarea}
            rows={4}
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
            className={
              length > DECLINE_NOTE_MAX_LENGTH
                ? 'text-sm text-destructive'
                : 'text-sm text-muted-foreground'
            }
          >
            {t('offers.decline.counter', {
              length,
              max: DECLINE_NOTE_MAX_LENGTH,
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
            {t('offers.cancel')}
          </Button>
          <Button type="submit" variant="destructive" disabled={busy}>
            {busy ? (
              <Spinner label={t('offers.decline.submitting')} showLabel />
            ) : (
              t('offers.decline.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
