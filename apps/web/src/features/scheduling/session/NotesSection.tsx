import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import type { SessionNote } from '@/features/tutor-portal/notes/api';
import {
  NOTE_TEXTAREA_CLASS,
  NoteBadge,
  NoteText,
} from '@/features/tutor-portal/notes/NotesView';
import {
  needsReload,
  reasonError,
  serverReasonError,
  trimmedLength,
  UNPUBLISH_REASON_MAX,
  type ReasonError,
} from '@/features/tutor-portal/notes/rules';

// The session's notes on the staff session page (#79 H3), rendered by the
// page only for academic_note.review holders (Finance never sees it): each
// note with its student, author, visibility badge and text. "Stop sharing"
// on a shared note opens a dialog that requires a reason (1-500 characters
// once trimmed); the tutor is notified and sees it. A write refused because
// the note changed offers Reload.

export type NotesSectionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; notes: readonly SessionNote[] };

export interface NotesSectionProps {
  state: NotesSectionState;
  timeZone: string;
  /** Stops sharing a note; resolves once done, or rejects with the error. */
  onStopSharing: (note: SessionNote, reason: string) => Promise<void>;
  onReload: () => void;
  /** Stories: open the dialog for this note. */
  initialStopping?: SessionNote | null;
}

export function NotesSection({
  state,
  timeZone,
  onStopSharing,
  onReload,
  initialStopping = null,
}: NotesSectionProps) {
  const { t } = useTranslation();
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const [stopping, setStopping] = useState<SessionNote | null>(initialStopping);
  const [notice, setNotice] = useState<string | null>(null);

  function close() {
    setStopping(null);
    window.setTimeout(() => {
      const target = trigger.current;
      if (target?.isConnected) {
        target.focus();
      } else {
        heading.current?.focus();
      }
    }, 0);
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={2} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.notes.length === 0) {
    body = <p className="text-sm">{t('notes.staff.empty')}</p>;
  } else {
    body = (
      <ul className="flex min-w-0 flex-col gap-3">
        {state.notes.map((note) => (
          <li
            key={`${note.id}-${note.version}`}
            className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3"
          >
            <p className="text-sm font-medium break-words">
              {t('notes.staff.student', { student: note.student.display_name })}
            </p>
            <div>
              <NoteBadge note={note} timeZone={timeZone} />
            </div>
            <NoteText note={note} timeZone={timeZone} />
            {note.can_unpublish ? (
              <div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={(event) => {
                    trigger.current = event.currentTarget;
                    setNotice(null);
                    setStopping(note);
                  }}
                >
                  {t('notes.staff.stopSharing')}
                </Button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2
        id={`${id}-title`}
        ref={heading}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {t('notes.staff.title')}
      </h2>
      {notice ? (
        <p role="status" className="text-sm font-medium">
          {notice}
        </p>
      ) : null}
      {body}
      <StopSharingDialog
        note={stopping}
        onCancel={close}
        onReload={() => {
          close();
          onReload();
        }}
        onConfirm={async (note, reason) => {
          await onStopSharing(note, reason);
          close();
          setNotice(t('notes.staff.done'));
        }}
      />
    </section>
  );
}

export interface StopSharingDialogProps {
  note: SessionNote | null;
  onConfirm: (note: SessionNote, reason: string) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
}

/** The staff unpublish (H3): a required reason with a counter. */
export function StopSharingDialog(props: StopSharingDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={props.note !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          props.onCancel();
        }
      }}
    >
      {props.note ? (
        <StopSharingContent {...props} note={props.note} sendingRef={sending} />
      ) : null}
    </Dialog>
  );
}

function StopSharingContent({
  note,
  onConfirm,
  onCancel,
  onReload,
  sendingRef,
}: StopSharingDialogProps & {
  note: SessionNote;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const id = useId();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<ReasonError | undefined>();
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const errorId = `${id}-error`;
  const counterId = `${id}-counter`;

  function message(code: ReasonError | undefined) {
    if (!code) {
      return undefined;
    }
    return code === 'reason_too_long'
      ? t('notes.validation.reasonTooLong', { max: UNPUBLISH_REASON_MAX })
      : t('notes.validation.reasonRequired');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sendingRef.current) {
      return;
    }
    setFailure(null);
    const code = reasonError(reason);
    setProblem(code);
    if (code) {
      textarea.current?.focus();
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm(note, reason.trim());
    } catch (caught) {
      const field = serverReasonError(caught);
      if (field) {
        setProblem(field);
        textarea.current?.focus();
      } else {
        setFailure(caught);
      }
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

  const length = trimmedLength(reason);
  return (
    <DialogContent
      onOpenAutoFocus={(event) => {
        event.preventDefault();
        textarea.current?.focus();
      }}
      onCloseAutoFocus={(event) => event.preventDefault()}
      onInteractOutside={(event) => event.preventDefault()}
      className="sm:max-w-lg"
    >
      <DialogHeader>
        <DialogTitle>{t('notes.staff.dialog.title')}</DialogTitle>
        <DialogDescription>
          {t('notes.staff.dialog.description')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        method="post"
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? (
          <ErrorAlert
            error={failure}
            onRetry={needsReload(failure) ? onReload : undefined}
            retryLabel={t('notes.reload')}
          />
        ) : null}
        <blockquote className="max-h-40 overflow-y-auto rounded-md border border-border p-3 text-sm break-words whitespace-pre-wrap">
          {note.body}
        </blockquote>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${id}-reason`} className="text-sm font-medium">
            {t('notes.staff.dialog.reason')}{' '}
            <span className="font-normal text-muted-foreground">
              {t('notes.staff.dialog.required')}
            </span>
          </label>
          <textarea
            id={`${id}-reason`}
            ref={textarea}
            rows={3}
            required
            aria-required
            value={reason}
            aria-invalid={problem ? true : undefined}
            aria-describedby={[problem ? errorId : undefined, counterId]
              .filter(Boolean)
              .join(' ')}
            onChange={(event) => setReason(event.target.value)}
            className={NOTE_TEXTAREA_CLASS}
          />
          <FieldError id={errorId} message={message(problem)} />
          <p
            id={counterId}
            className={
              length > UNPUBLISH_REASON_MAX
                ? 'text-sm text-destructive'
                : 'text-sm text-muted-foreground'
            }
          >
            {t('notes.staff.dialog.counter', {
              length,
              max: UNPUBLISH_REASON_MAX,
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
            {t('notes.staff.dialog.cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner label={t('notes.staff.dialog.sending')} showLabel />
            ) : (
              t('notes.staff.dialog.confirm')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
