import { useQuery } from '@tanstack/react-query';
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import {
  ACTIVE_LEVEL_OPTIONS_KEY,
  activeSubjectOptionsKey,
  fetchActiveLevelOptions,
  fetchActiveSubjectOptions,
  type CatalogOption,
  type QualificationItem,
  type TeachingSubject,
} from './api';

// The tutor profile's Qualifications section (#44 F1), `id="qualifications"`
// (the decision notifications link here). "Teaching subjects" lists the
// approved qualifications; the table lists every request with its status as
// a word, the request and decision dates and the reason of a rejection. For
// the tutor themselves: "Request qualification" opens a dialog with a level
// select and a subject select (active ones only, from the catalog API), and
// a pending row has "Cancel request" behind a confirmation. API errors
// (qualification_exists, subject_inactive) show in the dialog's alert.
// Presentational apart from the dialog's catalog queries: the page passes
// the list state and the actions; stories pass stubs.

export type QualificationsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly QualificationItem[] };

interface QualificationsSectionProps {
  state: QualificationsState;
  teachingSubjects: readonly TeachingSubject[];
  /** The caller is the tutor themselves (UX only; the API decides). */
  canRequest: boolean;
  /** Requests a subject; rejects with the error to show in the dialog. */
  onRequest: (subjectId: string) => Promise<void>;
  /** Cancels a pending request; rejects with the error to show. */
  onCancel: (item: QualificationItem) => Promise<void>;
  /** The dialog's catalog reads (stories pass stubs). */
  loadLevels?: (signal: AbortSignal) => Promise<CatalogOption[]>;
  loadSubjects?: (
    levelId: string,
    signal: AbortSignal,
  ) => Promise<CatalogOption[]>;
  /** Opens with the request dialog shown (stories). */
  initiallyRequesting?: boolean;
}

export function QualificationsSection({
  state,
  teachingSubjects,
  canRequest,
  onRequest,
  onCancel,
  loadLevels = fetchActiveLevelOptions,
  loadSubjects = fetchActiveSubjectOptions,
  initiallyRequesting = false,
}: QualificationsSectionProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const titleId = `${id}-title`;
  const tableTitleId = `${id}-table`;
  const teachingTitleId = `${id}-teaching`;
  const [requesting, setRequesting] = useState(
    initiallyRequesting && canRequest,
  );
  const [cancelling, setCancelling] = useState<QualificationItem | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [error, setError] = useState<unknown>(null);

  async function cancel() {
    if (!cancelling) {
      return;
    }
    setError(null);
    try {
      await onCancel(cancelling);
      setAnnouncement(t('tutors.qualifications.cancelled'));
    } catch (caught) {
      setError(caught);
    } finally {
      setCancelling(null);
    }
  }

  let table;
  if (state.status === 'loading') {
    table = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    table = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    table = <p className="text-sm">{t('tutors.qualifications.empty')}</p>;
  } else {
    const cancellable =
      canRequest && state.items.some((item) => item.status === 'pending');
    table = (
      <Table aria-labelledby={tableTitleId}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              {t('tutors.qualifications.subject')}
            </TableHead>
            <TableHead scope="col">
              {t('tutors.qualifications.level')}
            </TableHead>
            <TableHead scope="col">
              {t('tutors.qualifications.status')}
            </TableHead>
            <TableHead scope="col">
              {t('tutors.qualifications.requestedAt')}
            </TableHead>
            <TableHead scope="col">
              {t('tutors.qualifications.decidedAt')}
            </TableHead>
            <TableHead scope="col">
              {t('tutors.qualifications.reason')}
            </TableHead>
            {cancellable ? (
              <TableHead scope="col">
                {t('tutors.qualifications.actions')}
              </TableHead>
            ) : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.items.map((item) => (
            <TableRow key={item.id} data-qualification-id={item.id}>
              <TableCell className="whitespace-normal">
                {item.subject_name}
                {item.subject_is_active
                  ? null
                  : ` ${t('tutors.qualifications.subjectInactive')}`}
              </TableCell>
              <TableCell className="whitespace-normal">
                {item.education_level_name}
              </TableCell>
              <TableCell>
                <StatusBadge>
                  {t(`tutors.qualifications.statuses.${item.status}`)}
                </StatusBadge>
              </TableCell>
              <TableCell>
                {formatInstant(item.requested_at, 'dateTime')}
              </TableCell>
              <TableCell>
                {item.decided_at
                  ? formatInstant(item.decided_at, 'dateTime')
                  : null}
              </TableCell>
              <TableCell className="whitespace-normal break-words">
                {item.status === 'rejected' ? item.rejection_reason : null}
              </TableCell>
              {cancellable ? (
                <TableCell>
                  {item.status === 'pending' ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={t('tutors.qualifications.cancelFor', {
                        subject: item.subject_name,
                      })}
                      onClick={() => {
                        setError(null);
                        setAnnouncement('');
                        setCancelling(item);
                      }}
                    >
                      {t('tutors.qualifications.cancel')}
                    </Button>
                  ) : null}
                </TableCell>
              ) : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  }

  return (
    <section
      id="qualifications"
      aria-labelledby={titleId}
      className="flex scroll-mt-4 flex-col gap-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={titleId} className="text-lg font-semibold">
          {t('tutors.qualifications.title')}
        </h2>
        {canRequest ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setError(null);
              setAnnouncement('');
              setRequesting(true);
            }}
          >
            {t('tutors.qualifications.request')}
          </Button>
        ) : null}
      </div>
      <p className="text-sm">{t('tutors.qualifications.intro')}</p>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {error ? <ErrorAlert error={error} /> : null}
      <div className="flex flex-col gap-2">
        <h3 id={teachingTitleId} className="text-base font-semibold">
          {t('tutors.qualifications.teachingTitle')}
        </h3>
        {teachingSubjects.length === 0 ? (
          <p className="text-sm">{t('tutors.qualifications.teachingEmpty')}</p>
        ) : (
          <ul
            aria-labelledby={teachingTitleId}
            className="list-disc ps-5 text-sm"
          >
            {teachingSubjects.map((subject) => (
              <li key={subject.subject_id} className="break-words">
                {`${subject.subject_name} (${subject.education_level_name})`}
                {subject.subject_is_active
                  ? null
                  : ` ${t('tutors.qualifications.subjectInactive')}`}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex flex-col gap-2">
        <h3 id={tableTitleId} className="text-base font-semibold">
          {t('tutors.qualifications.title')}
        </h3>
        {table}
      </div>
      <RequestQualificationDialog
        open={requesting}
        loadLevels={loadLevels}
        loadSubjects={loadSubjects}
        onCancel={() => setRequesting(false)}
        onRequest={async (subjectId) => {
          await onRequest(subjectId);
          setRequesting(false);
          setAnnouncement(t('tutors.qualifications.requested'));
        }}
      />
      <CancelQualificationDialog
        item={cancelling}
        onConfirm={cancel}
        onKeep={() => setCancelling(null)}
      />
    </section>
  );
}

/** The status as a word in a bordered badge (never colour alone). */
function StatusBadge({ children }: { children: string }) {
  return (
    <span className="inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
      {children}
    </span>
  );
}

interface RequestQualificationDialogProps {
  open: boolean;
  loadLevels: (signal: AbortSignal) => Promise<CatalogOption[]>;
  loadSubjects: (
    levelId: string,
    signal: AbortSignal,
  ) => Promise<CatalogOption[]>;
  /** Sends the request; rejects with the error to show in the dialog. */
  onRequest: (subjectId: string) => Promise<void>;
  onCancel: () => void;
}

/** The request dialog: an education level select, then its subjects. */
export function RequestQualificationDialog({
  open,
  loadLevels,
  loadSubjects,
  onRequest,
  onCancel,
}: RequestQualificationDialogProps) {
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
        <RequestQualificationContent
          loadLevels={loadLevels}
          loadSubjects={loadSubjects}
          onRequest={onRequest}
          onCancel={onCancel}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function RequestQualificationContent({
  loadLevels,
  loadSubjects,
  onRequest,
  onCancel,
  sendingRef,
}: Omit<RequestQualificationDialogProps, 'open'> & {
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const id = useId();
  const levelId = `${id}-level`;
  const subjectFieldId = `${id}-subject`;
  const subjectErrorId = `${id}-subject-error`;
  const returnFocus = useRef<Element | null>(null);
  const subjectField = useRef<HTMLSelectElement>(null);
  const [level, setLevel] = useState('');
  const [subject, setSubject] = useState('');
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const levels = useQuery({
    queryKey: ACTIVE_LEVEL_OPTIONS_KEY,
    queryFn: ({ signal }) => loadLevels(signal),
  });
  const subjects = useQuery({
    queryKey: activeSubjectOptionsKey(level),
    queryFn: ({ signal }) => loadSubjects(level, signal),
    enabled: level !== '',
  });
  const subjectOptions = subjects.data ?? [];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sendingRef.current) {
      return;
    }
    if (!subject) {
      setMissing(true);
      subjectField.current?.focus();
      return;
    }
    setMissing(false);
    sendingRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await onRequest(subject);
    } catch (caught) {
      setError(caught);
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

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
    >
      <DialogHeader>
        <DialogTitle>{t('tutors.qualifications.requestTitle')}</DialogTitle>
        <DialogDescription>
          {t('tutors.qualifications.requestDescription')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex flex-col gap-4"
      >
        {error ? <ErrorAlert error={error} /> : null}
        {levels.isError ? (
          <ErrorAlert
            error={levels.error}
            onRetry={() => void levels.refetch()}
          />
        ) : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={levelId} className="text-sm font-medium">
            {t('tutors.qualifications.levelLabel')}
          </label>
          <select
            id={levelId}
            className={SELECT_CLASS}
            value={level}
            disabled={busy}
            onChange={(event) => {
              setLevel(event.target.value);
              setSubject('');
            }}
          >
            <option value="">
              {t('tutors.qualifications.levelPlaceholder')}
            </option>
            {(levels.data ?? []).map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={subjectFieldId} className="text-sm font-medium">
            {t('tutors.qualifications.subjectLabel')}
          </label>
          <select
            id={subjectFieldId}
            ref={subjectField}
            className={SELECT_CLASS}
            value={subject}
            disabled={busy || level === ''}
            aria-invalid={missing ? true : undefined}
            aria-describedby={missing ? subjectErrorId : undefined}
            onChange={(event) => {
              setSubject(event.target.value);
              setMissing(false);
            }}
          >
            <option value="">
              {t('tutors.qualifications.subjectPlaceholder')}
            </option>
            {subjectOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
          {level !== '' && subjects.isSuccess && subjectOptions.length === 0 ? (
            <p className="text-sm">{t('tutors.qualifications.subjectNone')}</p>
          ) : null}
          {subjects.isError ? (
            <ErrorAlert
              error={subjects.error}
              onRetry={() => void subjects.refetch()}
            />
          ) : null}
          <FieldError
            id={subjectErrorId}
            message={
              missing ? t('tutors.qualifications.subjectRequired') : undefined
            }
          />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onCancel}
          >
            {t('tutors.qualifications.close')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner label={t('tutors.qualifications.sending')} showLabel />
            ) : (
              t('tutors.qualifications.send')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

/** The confirmation of "Cancel request". */
function CancelQualificationDialog({
  item,
  onConfirm,
  onKeep,
}: {
  item: QualificationItem | null;
  onConfirm: () => Promise<void>;
  onKeep: () => void;
}) {
  const { t } = useTranslation();
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const returnFocus = useRef<Element | null>(null);

  async function confirm() {
    if (sending.current) {
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onKeep();
        }
      }}
    >
      {item ? (
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
        >
          <DialogHeader>
            <DialogTitle>{t('tutors.qualifications.cancelTitle')}</DialogTitle>
            <DialogDescription>
              {t('tutors.qualifications.cancelDescription', {
                subject: item.subject_name,
                level: item.education_level_name,
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onKeep}
            >
              {t('tutors.qualifications.cancelKeep')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? (
                <Spinner
                  label={t('tutors.qualifications.cancelling')}
                  showLabel
                />
              ) : (
                t('tutors.qualifications.cancelConfirm')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
