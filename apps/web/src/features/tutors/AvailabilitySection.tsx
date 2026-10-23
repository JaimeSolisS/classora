import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { WeeklyWindowsEditor } from '@/components/weekly-windows/WeeklyWindowsEditor';
import {
  hasWindowErrors,
  rowsOf,
  sameWindows,
  validateWindows,
  WINDOW_TIME_STEP_SECONDS,
  WINDOW_WEEKDAYS,
  windowFieldId,
  windowsOf,
  type WeeklyWindow,
  type WindowRow,
  type WindowRowErrors,
} from '@/components/weekly-windows/windows';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import type {
  AvailabilityChangeRequest,
  AvailabilityException,
  AvailabilityExceptionCreate,
  AvailabilitySettingsUpdate,
  ExceptionKind,
  TutorAvailability,
} from './api';
import { PendingChanges, SentForApprovalNotice } from './PendingChanges';
import {
  AvailabilitySettingsForm,
  AvailabilitySettingsList,
  type ClassroomOptionsState,
} from './AvailabilitySettingsForm';
import {
  AVAILABILITY_WINDOWS_MAX,
  EMPTY_EXCEPTION,
  exceptionBody,
  exceptionFieldOf,
  usesHours,
  validateException,
  type ExceptionErrors,
  type ExceptionField,
  type ExceptionFormValues,
} from './availabilityRules';

// The tutor profile's Availability section (#45 F1, F2), `id="availability"`.
// A help line names the center's time zone: every time is local wall-clock
// time there. "Weekly availability": for the tutor themselves and holders of
// tutor.manage, the shared WeeklyWindowsEditor (#39) saved with the loaded
// `version`; for other viewers, a per-weekday list. "Exceptions": a table
// (type as words, dates, hours or "All day", note) with Delete behind a
// confirmation, and "Add exception" opening a dialog (type, dates, "All
// day" — forced off for extra availability —, times, note). Errors:
// version_conflict with "Reload" and anything else in an alert region,
// field errors on their fields. A change that takes away time scheduled
// classes use (M4-18 #63 H1) is answered with a pending change request:
// the section announces "Sent for approval" with the first affected
// classes, the forms keep showing the current availability, and the
// "Changes waiting for approval" list (with Withdraw for editors) and the
// recent decisions (a rejection's reason) follow the exceptions.
// Between the weekly windows and the exceptions, the teaching limits and
// preferred classrooms (#197, AvailabilitySettingsForm), saved with the
// same `version`. Presentational: the page passes the query state and the
// actions.

export type AvailabilityState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; availability: TutorAvailability };

export interface AvailabilitySectionProps {
  state: AvailabilityState;
  /** The tutor themselves or tutor.manage (UX only; the API decides). */
  canEdit: boolean;
  /** The center's IANA time zone (the help line). */
  timeZone: string;
  /** Saves the weekly windows: null when applied, the change request when
   * sent for approval; rejects with the error to show. */
  onSaveWeekly: (
    version: number,
    windows: WeeklyWindow[],
  ) => Promise<AvailabilityChangeRequest | null>;
  /** Refetches the availability after a version conflict. */
  onReload: () => Promise<void>;
  /** The active classrooms, the preferred-room choices (editors only). */
  classrooms: ClassroomOptionsState;
  /** Saves the limits and preferred classrooms; rejects with the error to
   * show. */
  onSaveSettings: (body: AvailabilitySettingsUpdate) => Promise<void>;
  /** Adds an exception (null, or the change request sent for approval);
   * rejects with the error to show in the dialog. */
  onAddException: (
    body: AvailabilityExceptionCreate,
  ) => Promise<AvailabilityChangeRequest | null>;
  /** Deletes an exception (null, or the change request sent for
   * approval); rejects with the error to show. */
  onDeleteException: (
    item: AvailabilityException,
  ) => Promise<AvailabilityChangeRequest | null>;
  /** Withdraws a pending change request; rejects with the error to show. */
  onWithdrawChange: (request: AvailabilityChangeRequest) => Promise<void>;
  /** Opens with the exception dialog shown (stories). */
  initiallyAdding?: boolean;
  /** Opens showing this "Sent for approval" notice (stories). */
  initiallySent?: AvailabilityChangeRequest | null;
}

export function AvailabilitySection({
  state,
  canEdit,
  timeZone,
  onSaveWeekly,
  onReload,
  classrooms,
  onSaveSettings,
  onAddException,
  onDeleteException,
  onWithdrawChange,
  initiallyAdding = false,
  initiallySent = null,
}: AvailabilitySectionProps) {
  const { t } = useTranslation();
  const id = useId();
  const titleId = `${id}-title`;
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [adding, setAdding] = useState(initiallyAdding && canEdit);
  const [deleting, setDeleting] = useState<AvailabilityException | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [sent, setSent] = useState<AvailabilityChangeRequest | null>(
    initiallySent,
  );
  const [error, setError] = useState<unknown>(null);

  /** A write's outcome: applied (`done` is announced) or sent for
   * approval. */
  function outcome(request: AvailabilityChangeRequest | null, done: string) {
    setSent(request);
    setAnnouncement(request ? '' : done);
  }

  async function confirmDelete() {
    if (!deleting) {
      return;
    }
    setError(null);
    try {
      outcome(
        await onDeleteException(deleting),
        t('tutors.availability.deleted'),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setDeleting(null);
    }
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    body = (
      <>
        {canEdit ? (
          <WeeklyEditor
            availability={state.availability}
            onSave={async (version, windows) => {
              setSent(null);
              const request = await onSaveWeekly(version, windows);
              outcome(request, t('tutors.availability.saved'));
              return request;
            }}
            onReload={onReload}
          />
        ) : (
          <WeeklyList windows={state.availability.weekly} />
        )}
        {canEdit ? (
          <AvailabilitySettingsForm
            availability={state.availability}
            classrooms={classrooms}
            onSave={onSaveSettings}
            onReload={onReload}
          />
        ) : (
          <AvailabilitySettingsList availability={state.availability} />
        )}
        <ExceptionsTable
          items={state.availability.exceptions}
          canEdit={canEdit}
          onAdd={() => {
            setError(null);
            setAnnouncement('');
            setSent(null);
            setAdding(true);
          }}
          onDelete={(item) => {
            setError(null);
            setAnnouncement('');
            setSent(null);
            setDeleting(item);
          }}
        />
        <PendingChanges
          pending={state.availability.pending_changes}
          recent={state.availability.recent_changes}
          canEdit={canEdit}
          onWithdraw={onWithdrawChange}
          onWithdrawn={() => {
            setSent(null);
            setAnnouncement(t('tutors.availability.changes.withdrawn'));
          }}
          fallbackFocus={titleRef}
        />
      </>
    );
  }

  return (
    <section
      id="availability"
      aria-labelledby={titleId}
      className="flex scroll-mt-4 flex-col gap-4"
    >
      <h2
        id={titleId}
        ref={titleRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {t('tutors.availability.title')}
      </h2>
      <p className="text-sm">
        {t('tutors.availability.help', { zone: timeZone })}
      </p>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      <SentForApprovalNotice request={sent} />
      {error ? <ErrorAlert error={error} /> : null}
      {body}
      <AddExceptionDialog
        open={adding}
        onCancel={() => setAdding(false)}
        onAdd={async (body) => {
          const request = await onAddException(body);
          setAdding(false);
          outcome(request, t('tutors.availability.added'));
        }}
      />
      <DeleteExceptionDialog
        item={deleting}
        onConfirm={confirmDelete}
        onKeep={() => setDeleting(null)}
      />
    </section>
  );
}

/** The read-only weekly windows, one line per weekday that has any. */
function WeeklyList({ windows }: { windows: readonly WeeklyWindow[] }) {
  const { t } = useTranslation();
  const id = useId();
  const days = WINDOW_WEEKDAYS.map((weekday) => ({
    weekday,
    ranges: windows
      .filter((window) => window.weekday === weekday)
      .map((window) =>
        t('weeklyWindows.range', {
          start: window.starts_at,
          end: window.ends_at,
        }),
      ),
  })).filter((day) => day.ranges.length > 0);
  return (
    <div className="flex flex-col gap-2">
      <h3 id={id} className="text-base font-semibold">
        {t('tutors.availability.weeklyTitle')}
      </h3>
      {days.length === 0 ? (
        <p className="text-sm">{t('tutors.availability.weeklyEmpty')}</p>
      ) : (
        <dl
          aria-labelledby={id}
          className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm"
        >
          {days.map((day) => (
            <div key={day.weekday} className="contents">
              <dt className="font-medium">
                {t(`weeklyWindows.weekdays.${day.weekday}`)}
              </dt>
              <dd>{day.ranges.join(', ')}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

const WINDOW_FIELDS: Record<string, keyof WindowRowErrors> = {
  weekday: 'weekday',
  starts_at: 'startsAt',
  ends_at: 'endsAt',
};

/** The rows' errors of a server 422, or null when none is about a row. */
function serverWindowErrors(
  error: ApiError,
  rowCount: number,
): WindowRowErrors[] | null {
  const errors: WindowRowErrors[] = Array.from(
    { length: rowCount },
    () => ({}),
  );
  let found = false;
  for (const fieldError of error.fieldErrors) {
    const [, field, index, part] = fieldError.path;
    const row = typeof index === 'number' ? errors[index] : undefined;
    if (field !== 'windows' || !row) {
      continue;
    }
    row[WINDOW_FIELDS[String(part)] ?? 'startsAt'] =
      fieldError.code === 'window_overlap'
        ? 'weeklyWindows.validation.overlap'
        : 'weeklyWindows.validation.invalid';
    found = true;
  }
  return found ? errors : null;
}

function firstWindowError(
  editorId: string,
  rows: readonly WindowRow[],
  errors: readonly WindowRowErrors[],
): string | null {
  for (const [index, row] of rows.entries()) {
    const rowErrors = errors[index] ?? {};
    for (const field of ['weekday', 'startsAt', 'endsAt'] as const) {
      if (rowErrors[field]) {
        return windowFieldId(editorId, row.key, field);
      }
    }
  }
  return null;
}

/** The editable weekly windows; a new `version` (saved or reloaded)
 * replaces the rows. */
function WeeklyEditor({
  availability,
  onSave,
  onReload,
}: {
  availability: TutorAvailability;
  onSave: (
    version: number,
    windows: WeeklyWindow[],
  ) => Promise<AvailabilityChangeRequest | null>;
  onReload: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const editorId = `${id}-windows`;
  const [rows, setRows] = useState<WindowRow[]>(() =>
    rowsOf(availability.weekly),
  );
  const [errors, setErrors] = useState<WindowRowErrors[]>([]);
  const [checkRows, setCheckRows] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState('');
  const sending = useRef(false);

  // A new version from the server: the rows show it (adjusted while
  // rendering, so focus stays on the Save button).
  const [shownVersion, setShownVersion] = useState(availability.version);
  if (availability.version !== shownVersion) {
    setShownVersion(availability.version);
    setRows(rowsOf(availability.weekly));
    setErrors([]);
    setCheckRows(false);
  }

  function change(next: WindowRow[]) {
    setRows(next);
    setStatus('');
    setErrors(checkRows ? validateWindows(next) : []);
  }

  function focusFirst(found: readonly WindowRowErrors[]) {
    const target = firstWindowError(editorId, rows, found);
    if (target) {
      document.getElementById(target)?.focus();
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    setError(null);
    setStatus('');
    const found = validateWindows(rows);
    setErrors(found);
    setCheckRows(true);
    if (hasWindowErrors(found)) {
      focusFirst(found);
      return;
    }
    const windows = windowsOf(rows);
    if (sameWindows(windows, availability.weekly)) {
      setStatus(t('tutors.availability.unchanged'));
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      // On success the page stores the answer; its new version resets the
      // rows. Sent for approval, nothing changed: the rows show the current
      // windows again.
      const request = await onSave(availability.version, windows);
      if (request) {
        setRows(rowsOf(availability.weekly));
        setErrors([]);
        setCheckRows(false);
      }
    } catch (caught) {
      const fromServer =
        caught instanceof ApiError
          ? serverWindowErrors(caught, rows.length)
          : null;
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setError(caught);
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex flex-col gap-3"
    >
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={
            isConflict
              ? () => {
                  setError(null);
                  void onReload();
                }
              : undefined
          }
          retryLabel={isConflict ? t('tutors.availability.reload') : undefined}
        />
      ) : null}
      <WeeklyWindowsEditor
        id={editorId}
        rows={rows}
        onChange={change}
        errors={errors}
        disabled={busy}
        legend={t('tutors.availability.weeklyTitle')}
        help={t('tutors.availability.weeklyHelp')}
        emptyText={t('tutors.availability.weeklyEmpty')}
        max={AVAILABILITY_WINDOWS_MAX}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy}>
          {busy ? (
            <Spinner label={t('tutors.availability.saving')} showLabel />
          ) : (
            t('tutors.availability.save')
          )}
        </Button>
        <p role="status" className="text-sm">
          {status}
        </p>
      </div>
    </form>
  );
}

function ExceptionsTable({
  items,
  canEdit,
  onAdd,
  onDelete,
}: {
  items: readonly AvailabilityException[];
  canEdit: boolean;
  onAdd: () => void;
  onDelete: (item: AvailabilityException) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const describe = useExceptionText();
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id={id} className="text-base font-semibold">
          {t('tutors.availability.exceptionsTitle')}
        </h3>
        {canEdit ? (
          <Button type="button" variant="outline" onClick={onAdd}>
            {t('tutors.availability.add')}
          </Button>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground">
        {t('tutors.availability.exceptionsHelp')}
      </p>
      {items.length === 0 ? (
        <p className="text-sm">{t('tutors.availability.exceptionsEmpty')}</p>
      ) : (
        <Table aria-labelledby={id}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t('tutors.availability.kind')}</TableHead>
              <TableHead scope="col">
                {t('tutors.availability.dates')}
              </TableHead>
              <TableHead scope="col">
                {t('tutors.availability.hours')}
              </TableHead>
              <TableHead scope="col">{t('tutors.availability.note')}</TableHead>
              {canEdit ? (
                <TableHead scope="col">
                  {t('tutors.availability.actions')}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const text = describe(item);
              return (
                <TableRow key={item.id} data-exception-id={item.id}>
                  <TableCell>{text.kind}</TableCell>
                  <TableCell>{text.dates}</TableCell>
                  <TableCell>{text.hours}</TableCell>
                  <TableCell className="whitespace-normal break-words">
                    {item.note}
                  </TableCell>
                  {canEdit ? (
                    <TableCell>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('tutors.availability.deleteFor', {
                          kind: text.kind,
                          dates: text.dates,
                        })}
                        onClick={() => onDelete(item)}
                      >
                        {t('tutors.availability.delete')}
                      </Button>
                    </TableCell>
                  ) : null}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

/** An exception's type, dates and hours as words. */
function useExceptionText() {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  return (item: AvailabilityException) => ({
    kind: t(`tutors.availability.kinds.${item.kind}`),
    dates:
      item.starts_on === item.ends_on
        ? formatLocalDate(item.starts_on)
        : t('tutors.availability.dateRange', {
            start: formatLocalDate(item.starts_on),
            end: formatLocalDate(item.ends_on),
          }),
    hours:
      item.starts_at && item.ends_at
        ? t('weeklyWindows.range', { start: item.starts_at, end: item.ends_at })
        : t('tutors.availability.allDay'),
  });
}

interface AddExceptionDialogProps {
  open: boolean;
  /** Sends the exception; rejects with the error to show in the dialog. */
  onAdd: (body: AvailabilityExceptionCreate) => Promise<void>;
  onCancel: () => void;
}

/** The "Add exception" dialog. */
export function AddExceptionDialog({
  open,
  onAdd,
  onCancel,
}: AddExceptionDialogProps) {
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
        <AddExceptionContent
          onAdd={onAdd}
          onCancel={onCancel}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

const FIELD_ORDER: ExceptionField[] = [
  'startsOn',
  'endsOn',
  'startsAt',
  'endsAt',
  'note',
];

function AddExceptionContent({
  onAdd,
  onCancel,
  sendingRef,
}: Omit<AddExceptionDialogProps, 'open'> & {
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const id = useId();
  const ids: Record<ExceptionField | 'kind' | 'allDay', string> = {
    kind: `${id}-kind`,
    startsOn: `${id}-starts-on`,
    endsOn: `${id}-ends-on`,
    allDay: `${id}-all-day`,
    startsAt: `${id}-starts-at`,
    endsAt: `${id}-ends-at`,
    note: `${id}-note`,
  };
  const allDayHelpId = `${id}-all-day-help`;
  const returnFocus = useRef<Element | null>(null);
  const [values, setValues] = useState<ExceptionFormValues>(EMPTY_EXCEPTION);
  const [errors, setErrors] = useState<ExceptionErrors>({});
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const hours = usesHours(values);
  const extra = values.kind === 'available';

  function update(change: Partial<ExceptionFormValues>) {
    const next = { ...values, ...change };
    setValues(next);
    if (checked) {
      setErrors(validateException(next));
    }
  }

  function focusFirst(found: ExceptionErrors) {
    const field = FIELD_ORDER.find((name) => found[name]);
    if (field) {
      document.getElementById(ids[field])?.focus();
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sendingRef.current) {
      return;
    }
    const found = validateException(values);
    setErrors(found);
    setChecked(true);
    setError(null);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onAdd(exceptionBody(values));
    } catch (caught) {
      const fromServer: ExceptionErrors = {};
      if (caught instanceof ApiError) {
        for (const fieldError of caught.fieldErrors) {
          const field = exceptionFieldOf(fieldError.path);
          if (field) {
            fromServer[field] = 'tutors.availability.validation.invalid';
          }
        }
      }
      if (Object.keys(fromServer).length > 0) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setError(caught);
      }
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

  const message = (field: ExceptionField) =>
    errors[field] ? t(errors[field]) : undefined;
  const fieldProps = (field: ExceptionField) => ({
    id: ids[field],
    'aria-invalid': errors[field] ? true : undefined,
    'aria-describedby': errors[field] ? `${ids[field]}-error` : undefined,
  });

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto"
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
        <DialogTitle>{t('tutors.availability.dialogTitle')}</DialogTitle>
        <DialogDescription>
          {t('tutors.availability.dialogDescription')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex flex-col gap-4"
      >
        {error ? <ErrorAlert error={error} /> : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.kind} className="text-sm font-medium">
            {t('tutors.availability.kindLabel')}
          </label>
          <select
            id={ids.kind}
            className={SELECT_CLASS}
            value={values.kind}
            disabled={busy}
            onChange={(event) => {
              const kind = event.target.value as ExceptionKind;
              update({
                kind,
                allDay: kind === 'available' ? false : values.allDay,
              });
            }}
          >
            {(['unavailable', 'available'] as const).map((kind) => (
              <option key={kind} value={kind}>
                {t(`tutors.availability.kinds.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {(['startsOn', 'endsOn'] as const).map((field) => (
            <div key={field} className="flex flex-col gap-2">
              <label htmlFor={ids[field]} className="text-sm font-medium">
                {t(`tutors.availability.${field}`)}
              </label>
              <Input
                type="date"
                value={values[field]}
                disabled={busy}
                onChange={(event) => update({ [field]: event.target.value })}
                {...fieldProps(field)}
              />
              <FieldError id={`${ids[field]}-error`} message={message(field)} />
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Checkbox
              id={ids.allDay}
              checked={!extra && values.allDay}
              disabled={busy || extra}
              aria-describedby={extra ? allDayHelpId : undefined}
              onCheckedChange={(next) => update({ allDay: next === true })}
            />
            <label htmlFor={ids.allDay} className="text-sm font-medium">
              {t('tutors.availability.allDayLabel')}
            </label>
          </div>
          {extra ? (
            <p id={allDayHelpId} className="text-sm text-muted-foreground">
              {t('tutors.availability.allDayHelp')}
            </p>
          ) : null}
        </div>
        {hours ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {(['startsAt', 'endsAt'] as const).map((field) => (
              <div key={field} className="flex flex-col gap-2">
                <label htmlFor={ids[field]} className="text-sm font-medium">
                  {t(`tutors.availability.${field}`)}
                </label>
                <Input
                  type="time"
                  step={WINDOW_TIME_STEP_SECONDS}
                  value={values[field]}
                  disabled={busy}
                  onChange={(event) => update({ [field]: event.target.value })}
                  {...fieldProps(field)}
                />
                <FieldError
                  id={`${ids[field]}-error`}
                  message={message(field)}
                />
              </div>
            ))}
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.note} className="text-sm font-medium">
            {t('tutors.availability.noteLabel')}
          </label>
          <Input
            type="text"
            autoComplete="off"
            value={values.note}
            disabled={busy}
            onChange={(event) => update({ note: event.target.value })}
            {...fieldProps('note')}
          />
          <FieldError id={`${ids.note}-error`} message={message('note')} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onCancel}
          >
            {t('tutors.availability.close')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner label={t('tutors.availability.sending')} showLabel />
            ) : (
              t('tutors.availability.send')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

/** The confirmation of "Delete". */
function DeleteExceptionDialog({
  item,
  onConfirm,
  onKeep,
}: {
  item: AvailabilityException | null;
  onConfirm: () => Promise<void>;
  onKeep: () => void;
}) {
  const { t } = useTranslation();
  const describe = useExceptionText();
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

  const text = item ? describe(item) : null;
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onKeep();
        }
      }}
    >
      {text ? (
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
            <DialogTitle>{t('tutors.availability.deleteTitle')}</DialogTitle>
            <DialogDescription>
              {t('tutors.availability.deleteDescription', {
                kind: text.kind,
                dates: text.dates,
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
              {t('tutors.availability.deleteKeep')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? (
                <Spinner label={t('tutors.availability.deleting')} showLabel />
              ) : (
                t('tutors.availability.deleteConfirm')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
