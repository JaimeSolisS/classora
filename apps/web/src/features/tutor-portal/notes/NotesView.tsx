import { Lock, Users } from 'lucide-react';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
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
import type { SessionAttendance } from '@/features/tutor-portal/attendance/api';
import { ClassHeader } from '@/features/tutor-portal/attendance/AttendanceView';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import type { SessionNote } from './api';
import {
  BODY_MAX,
  bodyError,
  needsReload,
  serverBodyError,
  trimmedLength,
  type BodyError,
} from './rules';

// The tutor's notes page content (#79 H1), presentational and phone-first:
// the class header, a student selector when the class has several
// participants, this class's notes about the selected student (each with
// a badge: "Internal: never shown to families" or "Shared with family on
// {date}", an icon plus text, never colour only; "Edited"; the reason when
// a reviewer stopped sharing it), a composer ("Save internal note", with a
// 4000-character counter) and the actions the API's can_* flags allow:
// Edit (inline), Share with family (a confirmation dialog that shows the
// exact text and who will see it), Stop sharing and Delete (confirmed).
// Below, "Earlier notes about {student}" (other classes, read-only, with
// the author and "Load more"). Note text is always plain text. A write
// refused because the note changed shows "Note changed elsewhere" with
// Reload; the page maps the not-found codes.
// Focus (#79 QA D1): closing a dialog without confirming returns focus to
// the button that opened it (the notes heading if it is gone); a done
// action (save, edit, share, stop sharing, delete) moves it to the page's
// status message, a refused one to the failure; leaving the inline edit
// returns it to "Edit".

export type NotesListState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly T[] };

export interface NoteActions {
  onCreate: (body: string) => Promise<void>;
  onEdit: (note: SessionNote, body: string) => Promise<void>;
  onShare: (note: SessionNote) => Promise<void>;
  onStopSharing: (note: SessionNote) => Promise<void>;
  onDelete: (note: SessionNote) => Promise<void>;
  onReload: () => void;
}

export interface NotesHistory extends Partial<{
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}> {
  state: NotesListState<SessionNote>;
}

export interface NotesViewProps {
  session: SessionAttendance;
  timeZone: string;
  studentId: string;
  onStudentChange: (studentId: string) => void;
  notes: NotesListState<SessionNote>;
  history: NotesHistory;
  actions: NoteActions;
  /** Stories: a failed write to show. */
  initialFailure?: unknown;
  /** Stories: open the share dialog for this note. */
  initialSharing?: SessionNote | null;
}

export const NOTE_TEXTAREA_CLASS =
  'w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30';

function useWhen(timeZone: string) {
  const { i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  return (instant: string, style: 'dateTime' | 'date' = 'dateTime') =>
    formatInstant(instant, { timeZone, language, style });
}

/** The visibility badge: an icon and text (H1). */
export function NoteBadge({
  note,
  timeZone,
}: {
  note: Pick<SessionNote, 'visibility' | 'published_at'>;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const when = useWhen(timeZone);
  const shared = note.visibility === 'family' && note.published_at;
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border px-2 py-0.5 text-sm font-medium">
      {shared ? (
        <Users aria-hidden className="size-4 shrink-0" />
      ) : (
        <Lock aria-hidden className="size-4 shrink-0" />
      )}
      <span className="min-w-0 break-words">
        {shared
          ? t('notes.badges.shared', { date: when(note.published_at!, 'date') })
          : t('notes.badges.internal')}
      </span>
    </span>
  );
}

/** A note's text, author line, "Edited" and unpublish reason. */
export function NoteText({
  note,
  timeZone,
}: {
  note: SessionNote;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const when = useWhen(timeZone);
  return (
    <>
      <p className="text-sm break-words whitespace-pre-wrap">{note.body}</p>
      <p className="text-sm text-muted-foreground">
        {t('notes.byline', {
          name: note.author.display_name,
          when: when(note.created_at),
        })}
        {note.edited ? ` · ${t('notes.edited')}` : null}
      </p>
      {note.unpublish_reason ? (
        <p className="text-sm break-words">
          {t('notes.unpublishReason', { reason: note.unpublish_reason })}
        </p>
      ) : null}
    </>
  );
}

function Counter({
  id,
  value,
  max,
}: {
  id: string;
  value: string;
  max: number;
}) {
  const { t } = useTranslation();
  const length = trimmedLength(value);
  return (
    <p
      id={id}
      className={
        length > max
          ? 'text-sm text-destructive'
          : 'text-sm text-muted-foreground'
      }
    >
      {t('notes.composer.counter', { length, max })}
    </p>
  );
}

function useBodyMessage() {
  const { t } = useTranslation();
  return (code: BodyError | undefined) =>
    code === 'too_long'
      ? t('notes.validation.tooLong', { max: BODY_MAX })
      : code
        ? t('notes.validation.bodyRequired')
        : undefined;
}

interface BodyFieldProps {
  label: string;
  hint?: string;
  submitLabel: string;
  busyLabel: string;
  initial?: string;
  onSubmit: (body: string) => Promise<void>;
  onFailure: (error: unknown) => void;
  onCancel?: () => void;
  /** Clears the text after a successful submit (the composer). */
  resetOnSuccess?: boolean;
  /** Focuses the text when it appears (inline edit). */
  focusOnMount?: boolean;
}

/** A labelled textarea with its counter and validation (H1). */
function BodyField({
  label,
  hint,
  submitLabel,
  busyLabel,
  initial = '',
  onSubmit,
  onFailure,
  onCancel,
  resetOnSuccess = false,
  focusOnMount = false,
}: BodyFieldProps) {
  const { t } = useTranslation();
  const id = useId();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [body, setBody] = useState(initial);
  const [problem, setProblem] = useState<BodyError | undefined>();
  const [busy, setBusy] = useState(false);
  const message = useBodyMessage();
  const errorId = `${id}-error`;
  const counterId = `${id}-counter`;
  const hintId = `${id}-hint`;

  useEffect(() => {
    if (focusOnMount) {
      textarea.current?.focus();
    }
  }, [focusOnMount]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    const code = bodyError(body);
    setProblem(code);
    if (code) {
      textarea.current?.focus();
      return;
    }
    setBusy(true);
    try {
      await onSubmit(body.trim());
      if (resetOnSuccess) {
        setBody('');
      }
    } catch (caught) {
      const field = serverBodyError(caught);
      if (field) {
        setProblem(field);
        textarea.current?.focus();
      } else {
        onFailure(caught);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      noValidate
      method="post"
      onSubmit={(event) => void submit(event)}
      className="flex min-w-0 flex-col gap-2"
    >
      <label htmlFor={`${id}-body`} className="text-sm font-medium">
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
      <textarea
        id={`${id}-body`}
        ref={textarea}
        rows={4}
        value={body}
        aria-invalid={problem ? true : undefined}
        aria-describedby={[
          hint ? hintId : undefined,
          problem ? errorId : undefined,
          counterId,
        ]
          .filter(Boolean)
          .join(' ')}
        onChange={(event) => setBody(event.target.value)}
        className={NOTE_TEXTAREA_CLASS}
      />
      <FieldError id={errorId} message={message(problem)} />
      <Counter id={counterId} value={body} max={BODY_MAX} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? <Spinner label={busyLabel} showLabel /> : submitLabel}
        </Button>
        {onCancel ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onCancel}
          >
            {t('notes.actions.cancel')}
          </Button>
        ) : null}
      </div>
    </form>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  busyLabel: string;
  cancelLabel: string;
  children?: ReactNode;
  destructive?: boolean;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

/** A confirmation (share, delete): one request per confirm. The view moves
 * focus when it closes (the trigger on cancel, the outcome on confirm). */
function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  busyLabel,
  cancelLabel,
  children,
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) {
          onCancel();
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
          {children}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onCancel}
            >
              {cancelLabel}
            </Button>
            <Button
              type="button"
              variant={destructive ? 'destructive' : 'default'}
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void onConfirm().finally(() => setBusy(false));
              }}
            >
              {busy ? <Spinner label={busyLabel} showLabel /> : confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

/** The share confirmation (H1): the exact text and who will see it. */
export function ShareNoteDialog({
  note,
  onConfirm,
  onCancel,
}: {
  note: SessionNote | null;
  onConfirm: (note: SessionNote) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <ConfirmDialog
      open={note !== null}
      title={t('notes.share.title')}
      description={t('notes.share.description', {
        student: note?.student.display_name ?? '',
      })}
      confirmLabel={t('notes.share.confirm')}
      busyLabel={t('notes.share.sharing')}
      cancelLabel={t('notes.share.cancel')}
      onConfirm={() => (note ? onConfirm(note) : Promise.resolve())}
      onCancel={onCancel}
    >
      <figure className="flex min-w-0 flex-col gap-1">
        <figcaption id={`${id}-text`} className="text-sm font-medium">
          {t('notes.share.text')}
        </figcaption>
        <blockquote
          aria-labelledby={`${id}-text`}
          className="max-h-64 overflow-y-auto rounded-md border border-border p-3 text-sm break-words whitespace-pre-wrap"
        >
          {note?.body}
        </blockquote>
      </figure>
    </ConfirmDialog>
  );
}

function NoteItem({
  note,
  timeZone,
  actions,
  onFailure,
  onShareRequest,
  onDeleteRequest,
}: {
  note: SessionNote;
  timeZone: string;
  actions: NoteActions;
  onFailure: (error: unknown) => void;
  onShareRequest: (note: SessionNote, trigger: HTMLElement) => void;
  onDeleteRequest: (note: SessionNote, trigger: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  // #79 QA D1: leaving the inline edit returns focus to "Edit" (a saved
  // edit then moves it on to the page's notice).
  useEffect(() => {
    if (!editing && wasEditing.current) {
      editButton.current?.focus();
    }
    wasEditing.current = editing;
  }, [editing]);
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3">
      <div>
        <NoteBadge note={note} timeZone={timeZone} />
      </div>
      {editing ? (
        <BodyField
          label={t('notes.actions.editLabel')}
          submitLabel={t('notes.actions.save')}
          busyLabel={t('notes.actions.saving')}
          initial={note.body}
          focusOnMount
          onSubmit={async (body) => {
            await actions.onEdit(note, body);
            setEditing(false);
          }}
          onFailure={onFailure}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <NoteText note={note} timeZone={timeZone} />
      )}
      {!editing &&
      (note.can_edit ||
        note.can_publish ||
        note.can_unpublish ||
        note.can_delete) ? (
        <div className="flex flex-wrap gap-2">
          {note.can_edit ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              ref={editButton}
              onClick={() => setEditing(true)}
            >
              {t('notes.actions.edit')}
            </Button>
          ) : null}
          {note.can_publish ? (
            <Button
              type="button"
              size="sm"
              onClick={(event) => onShareRequest(note, event.currentTarget)}
            >
              {t('notes.actions.share')}
            </Button>
          ) : null}
          {note.can_unpublish ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={stopping}
              onClick={() => {
                setStopping(true);
                actions
                  .onStopSharing(note)
                  .catch(onFailure)
                  .finally(() => setStopping(false));
              }}
            >
              {stopping ? (
                <Spinner label={t('notes.actions.stopSharing')} showLabel />
              ) : (
                t('notes.actions.stopSharing')
              )}
            </Button>
          ) : null}
          {note.can_delete ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={(event) => onDeleteRequest(note, event.currentTarget)}
            >
              {t('notes.actions.delete')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function NotesView({
  session,
  timeZone,
  studentId,
  onStudentChange,
  notes,
  history,
  actions,
  initialFailure = null,
  initialSharing = null,
}: NotesViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const when = useWhen(timeZone);
  const [failure, setFailure] = useState<unknown>(initialFailure);
  const [notice, setNotice] = useState<string | null>(null);
  const [sharing, setSharing] = useState<SessionNote | null>(initialSharing);
  const [deleting, setDeleting] = useState<SessionNote | null>(null);
  // #79 QA D1: where focus goes once a dialog closes or an action is done.
  const trigger = useRef<HTMLElement | null>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [focusTarget, setFocusTarget] = useState<{
    id: number;
    to: 'notice' | 'failure' | 'trigger';
  } | null>(null);

  /** Moves focus after the render that shows the target (and after a
   * dialog has closed): the notice, the failure, or the dialog's trigger
   * (the notes heading when the trigger is gone). */
  function focusNext(to: 'notice' | 'failure' | 'trigger') {
    setFocusTarget((current) => ({ id: (current?.id ?? 0) + 1, to }));
  }

  useEffect(() => {
    if (!focusTarget) {
      return;
    }
    const timer = window.setTimeout(() => {
      const target =
        focusTarget.to === 'notice'
          ? noticeRef.current
          : focusTarget.to === 'failure'
            ? failureRef.current
            : trigger.current?.isConnected
              ? trigger.current
              : null;
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget]);
  const students = session.items.map((item) => item.student);
  const student = students.find((s) => s.id === studentId) ?? students[0];
  const name = student?.display_name ?? '';

  function done(text: string) {
    setFailure(null);
    setNotice(text);
    focusNext('notice');
  }

  function failed(error: unknown) {
    setNotice(null);
    setFailure(error);
    focusNext('failure');
  }

  const wrapped: NoteActions = {
    ...actions,
    onEdit: async (note, body) => {
      await actions.onEdit(note, body);
      done(t('notes.notices.edited'));
    },
    onStopSharing: async (note) => {
      await actions.onStopSharing(note);
      done(t('notes.notices.stopped'));
    },
  };

  let list: ReactNode;
  if (notes.status === 'loading') {
    list = <LoadingState rows={2} />;
  } else if (notes.status === 'error') {
    list = <ErrorAlert error={notes.error} onRetry={notes.onRetry} />;
  } else if (notes.items.length === 0) {
    list = <p className="text-sm">{t('notes.empty', { student: name })}</p>;
  } else {
    list = (
      <ul className="flex min-w-0 flex-col gap-3">
        {notes.items.map((note) => (
          <NoteItem
            key={`${note.id}-${note.version}`}
            note={note}
            timeZone={timeZone}
            actions={wrapped}
            onFailure={failed}
            onShareRequest={(note, from) => {
              trigger.current = from;
              setSharing(note);
            }}
            onDeleteRequest={(note, from) => {
              trigger.current = from;
              setDeleting(note);
            }}
          />
        ))}
      </ul>
    );
  }

  const earlier = history.state;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ClassHeader attendance={session} timeZone={timeZone} />
      {students.length > 1 ? (
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={`${id}-student`} className="text-sm font-medium">
            {t('notes.studentPicker')}
          </label>
          <select
            id={`${id}-student`}
            value={student?.id ?? ''}
            onChange={(event) => onStudentChange(event.target.value)}
            className="h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring sm:max-w-xs md:text-sm dark:bg-input/30"
          >
            {students.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {notice ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {notice}
        </p>
      ) : null}
      {failure ? (
        <div ref={failureRef} tabIndex={-1} className="outline-none">
          {needsReload(failure) ? (
            <div
              role="alert"
              className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive p-3 text-sm text-destructive"
            >
              <span>{t('notes.changed')}</span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setFailure(null);
                  actions.onReload();
                }}
              >
                {t('notes.reload')}
              </Button>
            </div>
          ) : (
            <ErrorAlert error={failure} />
          )}
        </div>
      ) : null}
      <section
        aria-labelledby={`${id}-notes`}
        className="flex min-w-0 flex-col gap-3"
      >
        <h2
          id={`${id}-notes`}
          ref={headingRef}
          tabIndex={-1}
          className="text-lg font-semibold break-words outline-none"
        >
          {t('notes.forStudent', { student: name })}
        </h2>
        {list}
      </section>
      {student ? (
        <BodyField
          key={student.id}
          label={t('notes.composer.label', { student: name })}
          hint={t('notes.composer.hint')}
          submitLabel={t('notes.composer.save')}
          busyLabel={t('notes.composer.saving')}
          resetOnSuccess
          onSubmit={async (body) => {
            await actions.onCreate(body);
            done(t('notes.composer.saved'));
          }}
          onFailure={failed}
        />
      ) : null}
      <section
        aria-labelledby={`${id}-history`}
        className="flex min-w-0 flex-col gap-3"
      >
        <h2 id={`${id}-history`} className="text-lg font-semibold break-words">
          {t('notes.history.title', { student: name })}
        </h2>
        {earlier.status === 'loading' ? (
          <LoadingState rows={2} />
        ) : earlier.status === 'error' ? (
          <ErrorAlert error={earlier.error} onRetry={earlier.onRetry} />
        ) : earlier.items.length === 0 ? (
          <p className="text-sm">{t('notes.history.empty')}</p>
        ) : (
          <ul className="flex min-w-0 flex-col gap-3">
            {earlier.items.map((note) => (
              <li
                key={note.id}
                className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3"
              >
                <p className="text-sm font-medium break-words">
                  {t('notes.history.session', {
                    subject: note.subject.name,
                    when: when(note.session_starts_at),
                  })}
                </p>
                <div>
                  <NoteBadge note={note} timeZone={timeZone} />
                </div>
                <NoteText note={note} timeZone={timeZone} />
              </li>
            ))}
          </ul>
        )}
        {earlier.status === 'ready' && history.hasMore ? (
          <div>
            <Button
              type="button"
              variant="outline"
              disabled={history.loadingMore}
              onClick={history.onLoadMore}
            >
              {history.loadingMore ? (
                <Spinner label={t('notes.history.loadingMore')} showLabel />
              ) : (
                t('notes.history.loadMore')
              )}
            </Button>
          </div>
        ) : null}
      </section>
      <ShareNoteDialog
        note={sharing}
        onCancel={() => {
          setSharing(null);
          focusNext('trigger');
        }}
        onConfirm={async (note) => {
          try {
            await actions.onShare(note);
            setSharing(null);
            done(t('notes.notices.shared'));
          } catch (caught) {
            setSharing(null);
            failed(caught);
          }
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={t('notes.deleteDialog.title')}
        description={t('notes.deleteDialog.description')}
        confirmLabel={t('notes.deleteDialog.confirm')}
        busyLabel={t('notes.deleteDialog.deleting')}
        cancelLabel={t('notes.deleteDialog.cancel')}
        destructive
        onCancel={() => {
          setDeleting(null);
          focusNext('trigger');
        }}
        onConfirm={async () => {
          if (!deleting) {
            return;
          }
          try {
            await actions.onDelete(deleting);
            setDeleting(null);
            done(t('notes.notices.deleted'));
          } catch (caught) {
            setDeleting(null);
            failed(caught);
          }
        }}
      />
    </div>
  );
}
