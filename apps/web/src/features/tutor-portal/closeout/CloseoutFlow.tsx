import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type { AttendanceMark } from '@/features/tutor-portal/attendance/api';
import {
  AttendanceView,
  type AttendanceViewState,
} from '@/features/tutor-portal/attendance/AttendanceView';
import { StatusLabel } from '@/features/tutor-portal/attendance/StatusLabel';
import { toLanguage } from '@/i18n/language';
import { formatInstant, formatLocalDate, toLocalDate } from '@/lib/time';
import type { CloseoutSummary, SessionCloseoutView } from './api';
import {
  EMPTY_SUMMARY,
  MINUTES_MAX,
  MINUTES_MIN,
  NOTE_MAX,
  SUMMARY_FIELDS,
  SUMMARY_MAX,
  allAbsent,
  editAllowed,
  isRequired,
  minutesError,
  noteError,
  serverSummaryErrors,
  summaryBody,
  summaryDraftOf,
  summaryErrors,
  unmarkedNames,
  type EditMode,
  type SummaryDraft,
  type SummaryErrors,
  type SummaryField,
} from './rules';

// The tutor's guided closeout (#77 G1), presentational. A header (subject,
// local date and time in the center zone, state), then:
// - a submitted closeout: the read-only view ("Submitted for review", the
//   minutes, the summary);
// - a blocked session: why (not started: when it opens; not closable: the
//   state; completed without a closeout: "No closeout recorded");
// - otherwise a 4-step flow whose current step is announced
//   (`aria-current="step"`, "Step n of 4"): 1. attendance (#76's form,
//   saved through its PUT; Next waits until everyone is marked and lists
//   who is missing), 2. teaching time (minutes 0-720, prefilled with the
//   scheduled minutes), 3. the summary (Topics, Performance, Homework
//   (optional), Next steps; required marks disappear when everyone is
//   absent; counters to 2000), 4. a review with Edit links and "Submit for
//   review". Typed text survives step changes (no draft is stored); the
//   page asks before leaving with typed text (`onDirtyChange`). Server
//   errors map back: version conflict → Reload; attendance incomplete →
//   step 1 with the names; a 422 → the fields.
// #305: a recorded closeout shows its review status as a view with a
// heading ("Submitted for review", "Correction under review", "Changes
// requested by the reviewer" with the reason, "Approved · n min"), driven
// only by the API's GET; the view's action ("Edit and resubmit" when
// `can_resubmit`, "Request a correction" when `can_request_correction`)
// opens the same 4 steps in place as an edit mode, prefilled from the
// closeout (minutes, summary). Step 1 is #76's form (editable in
// `resubmit`, read-only in `correction`, as the attendance GET says);
// step 4 adds an optional "Note for the reviewer" (resubmit) or a
// required "Reason for the correction". Cancel returns to the view (asking
// first when something was typed).

export type CloseoutFlowState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; closeout: SessionCloseoutView };

export interface CloseoutSubmission {
  actual_minutes: number;
  summary: Partial<Record<SummaryField, string>>;
  /** #305: the note (resubmit) or the reason (correction), trimmed. */
  reason?: string;
}

type FlowMode = 'submit' | EditMode;

export interface CloseoutFlowProps {
  state: CloseoutFlowState;
  /** The session's attendance (step 1). */
  attendance: AttendanceViewState;
  timeZone: string;
  /** Saves attendance marks (#76 PUT); resolves once reloaded. */
  onSaveAttendance: (marks: AttendanceMark[]) => Promise<void>;
  /** Reloads the attendance and the closeout. */
  onReload: () => void;
  /** Submits; resolves once saved, rejects with the error. */
  onSubmit: (submission: CloseoutSubmission) => Promise<void>;
  /** Whether typed text would be lost by leaving. */
  onDirtyChange?: (dirty: boolean) => void;
  /** The first step shown (stories). */
  initialStep?: Step;
  /** A prefilled summary (stories). */
  initialSummary?: SummaryDraft;
  /** #305: the edit mode shown in place of the status view (null: the view). */
  editMode?: EditMode | null;
  /** Opens an edit mode, or returns to the view (null). */
  onEditModeChange?: (mode: EditMode | null) => void;
  /** #305: resubmits or requests a correction; rejects with the error. */
  onResubmit?: (
    submission: CloseoutSubmission,
    mode: EditMode,
  ) => Promise<void>;
  /** An alert over the status view (#305 B1: already under review). */
  viewAlert?: string | null;
  /** A typed note or reason (stories). */
  initialNote?: string;
}

export type Step = 1 | 2 | 3 | 4;

const STEPS: readonly Step[] = [1, 2, 3, 4];
const STEP_KEYS = {
  1: 'closeout.steps.attendance',
  2: 'closeout.steps.time',
  3: 'closeout.steps.summary',
  4: 'closeout.steps.review',
} as const;
const FIELD_KEYS = {
  topics: 'closeout.fields.topics',
  performance: 'closeout.fields.performance',
  homework: 'closeout.fields.homework',
  next_steps: 'closeout.fields.next_steps',
} as const;

function useWhen(timeZone: string) {
  const { i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  return {
    language,
    when: (instant: string, style: 'dateTime' | 'time' = 'dateTime') =>
      formatInstant(instant, { timeZone, language, style }),
  };
}

export function CloseoutFlow(props: CloseoutFlowProps) {
  const { state, timeZone, editMode, onEditModeChange, viewAlert } = props;
  // Cancel returns focus to the view's heading (#305 F2).
  const [focusView, setFocusView] = useState(false);
  if (state.status === 'loading') {
    return <LoadingState rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { closeout } = state;
  let body: ReactNode;
  if (closeout.closeout && editAllowed(closeout, editMode)) {
    body = (
      <Steps
        key={editMode}
        {...props}
        closeout={closeout}
        mode={editMode}
        onCancel={() => {
          setFocusView(true);
          onEditModeChange?.(null);
        }}
      />
    );
  } else if (closeout.closeout) {
    body = (
      <CloseoutStatusView
        closeout={closeout}
        timeZone={timeZone}
        alert={viewAlert}
        focusHeading={focusView}
        onEdit={
          onEditModeChange
            ? (mode) => {
                setFocusView(false);
                onEditModeChange(mode);
              }
            : undefined
        }
      />
    );
  } else if (closeout.can_submit) {
    body = <Steps {...props} closeout={closeout} mode="submit" />;
  } else {
    body = <BlockMessage closeout={closeout} timeZone={timeZone} />;
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <SessionHeader closeout={closeout} timeZone={timeZone} />
      {body}
    </div>
  );
}

function SessionHeader({
  closeout,
  timeZone,
}: {
  closeout: SessionCloseoutView;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const { language, when } = useWhen(timeZone);
  const date = formatLocalDate(toLocalDate(closeout.starts_at, timeZone), {
    language,
    style: 'long',
  });
  return (
    <section className="flex min-w-0 flex-col gap-1">
      <h2 className="text-lg font-semibold break-words">
        {t('scheduling.calendar.dialog.subjectValue', {
          subject: closeout.subject.name,
          level: closeout.subject.education_level_name,
        })}
      </h2>
      <p className="text-sm">
        {t('attendance.when', {
          date,
          start: when(closeout.starts_at, 'time'),
          end: when(closeout.ends_at, 'time'),
          zone: timeZone,
        })}
      </p>
      <p className="text-sm">
        {t('attendance.state', {
          state: t(`scheduling.states.${closeout.state}`),
        })}
      </p>
    </section>
  );
}

function BlockMessage({
  closeout,
  timeZone,
}: {
  closeout: SessionCloseoutView;
  timeZone: string;
}) {
  const { t } = useTranslation();
  const { when } = useWhen(timeZone);
  let text: string;
  switch (closeout.block) {
    case 'session_not_started':
      text = t('closeout.blocked.session_not_started', {
        time: when(closeout.starts_at),
      });
      break;
    case 'session_not_closable':
      text = t('closeout.blocked.session_not_closable', {
        state: t(`scheduling.states.${closeout.state}`),
      });
      break;
    case 'closeout_already_submitted':
      text = t('closeout.noCloseout');
      break;
    default:
      text = t('closeout.blocked.closeout_tutor_only');
  }
  return (
    <p className="rounded-md border border-border p-3 text-sm font-medium">
      {text}
    </p>
  );
}

/** The summary as a definition list (the read-only view and the review). */
export function SummaryList({
  summary,
  edit,
}: {
  summary: CloseoutSummary;
  edit?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <dl className="grid min-w-0 gap-3 text-sm">
      {SUMMARY_FIELDS.map((field) => (
        <div key={field} className="flex min-w-0 flex-col gap-1">
          <dt className="font-medium">{t(FIELD_KEYS[field])}</dt>
          <dd className="break-words whitespace-pre-line">
            {summary[field] ?? t('closeout.empty')}
          </dd>
        </div>
      ))}
      {edit}
    </dl>
  );
}

/**
 * #305: a recorded closeout's status view (the table in the issue), only
 * from the GET: the heading, the reviewer's reason or the approval, the
 * minutes and summary, and the action the flags allow.
 */
export function CloseoutStatusView({
  closeout,
  timeZone,
  alert,
  focusHeading = false,
  onEdit,
}: {
  closeout: SessionCloseoutView;
  timeZone: string;
  alert?: string | null;
  focusHeading?: boolean;
  onEdit?: (mode: EditMode) => void;
}) {
  const { t } = useTranslation();
  const { language } = useWhen(timeZone);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusHeading) {
      heading.current?.focus();
    }
  }, [focusHeading]);
  const record = closeout.closeout;
  if (!record) {
    return <p className="text-sm font-medium">{t('closeout.noCloseout')}</p>;
  }
  const approval = record.approval;
  const inForce = approval
    ? t('closeout.tutorView.inForce', { minutes: approval.approved_minutes })
    : null;
  let title: string;
  let lines: ReactNode = null;
  let action: EditMode | null = null;
  if (record.status === 'changes_requested') {
    title = t('closeout.tutorView.changesRequested');
    lines = (
      <>
        {record.review.reason ? (
          <p className="text-sm break-words whitespace-pre-line">
            {record.review.reason}
          </p>
        ) : null}
        {inForce ? <p className="text-sm">{inForce}</p> : null}
      </>
    );
    action = closeout.can_resubmit ? 'resubmit' : null;
  } else if (record.status === 'approved' && approval) {
    title = t('closeout.tutorView.approved', {
      minutes: approval.approved_minutes,
    });
    lines = (
      <p className="text-sm">
        {t('closeout.tutorView.approvedBy', {
          name: approval.approved_by.display_name,
          date: formatLocalDate(toLocalDate(approval.approved_at, timeZone), {
            language,
            style: 'long',
          }),
        })}
      </p>
    );
    action = closeout.can_request_correction ? 'correction' : null;
  } else if (approval) {
    title = t('closeout.tutorView.correctionUnderReview');
    lines = (
      <>
        <p className="text-sm">{inForce}</p>
        {record.review.tutor_note ? (
          <p className="text-sm break-words whitespace-pre-line">
            {t('closeout.tutorView.yourReason', {
              reason: record.review.tutor_note,
            })}
          </p>
        ) : null}
      </>
    );
  } else {
    title = t('closeout.tutorView.underReview');
  }
  return (
    <section className="flex min-w-0 flex-col gap-4">
      {alert ? (
        <p
          role="alert"
          className="rounded-md border border-destructive p-3 text-sm font-medium"
        >
          {alert}
        </p>
      ) : null}
      <div className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-3">
        <h3
          ref={heading}
          tabIndex={-1}
          className="text-base font-semibold break-words outline-none"
        >
          {title}
        </h3>
        {lines}
        {action && onEdit ? (
          <div>
            <Button type="button" onClick={() => onEdit(action)}>
              {action === 'resubmit'
                ? t('closeout.tutorView.resubmit')
                : t('closeout.tutorView.requestCorrection')}
            </Button>
          </div>
        ) : null}
      </div>
      {record.status !== 'approved' ? (
        <dl className="grid gap-1 text-sm">
          <dt className="font-medium">{t('closeout.actualMinutes')}</dt>
          <dd>
            {t('closeout.minutesOfScheduled', {
              actual: record.actual_minutes ?? 0,
              scheduled: closeout.scheduled_minutes,
            })}
          </dd>
        </dl>
      ) : null}
      {record.summary ? (
        <SummaryList summary={record.summary} />
      ) : (
        <p className="text-sm">{t('closeout.summaryHidden')}</p>
      )}
    </section>
  );
}

function Stepper({ step }: { step: Step }) {
  const { t } = useTranslation();
  return (
    <ol
      aria-label={t('closeout.stepsLabel')}
      className="flex flex-wrap gap-2 text-sm"
    >
      {STEPS.map((item) => (
        <li
          key={item}
          aria-current={item === step ? 'step' : undefined}
          className={
            item === step
              ? 'rounded-md border border-ring px-2 py-1 font-semibold'
              : 'rounded-md border border-border px-2 py-1 text-muted-foreground'
          }
        >
          {item}. {t(STEP_KEYS[item])}
        </li>
      ))}
    </ol>
  );
}

function Steps({
  closeout,
  attendance,
  timeZone,
  onSaveAttendance,
  onReload,
  onSubmit,
  onResubmit,
  onDirtyChange,
  initialStep = 1,
  initialSummary,
  initialNote = '',
  mode,
  onCancel,
}: CloseoutFlowProps & {
  closeout: SessionCloseoutView;
  mode: FlowMode;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const noteField = useRef<HTMLTextAreaElement>(null);
  const editing = mode !== 'submit';
  // The baseline the dirty check compares against (#305 A2): the scheduled
  // minutes and an empty summary for a new closeout, the closeout's own
  // values in an edit mode.
  const [baseline] = useState(() => ({
    minutes: String(
      editing
        ? (closeout.closeout?.actual_minutes ?? closeout.scheduled_minutes)
        : closeout.scheduled_minutes,
    ),
    summary: editing
      ? summaryDraftOf(closeout.closeout?.summary ?? null)
      : EMPTY_SUMMARY,
  }));
  const [step, setStep] = useState<Step>(initialStep);
  const [minutes, setMinutes] = useState(baseline.minutes);
  const [minutesTouched, setMinutesTouched] = useState(false);
  const [summary, setSummary] = useState<SummaryDraft>(
    initialSummary ?? baseline.summary,
  );
  const [summaryTouched, setSummaryTouched] = useState(false);
  const [serverErrors, setServerErrors] = useState<SummaryErrors>({});
  const [note, setNote] = useState(initialNote);
  const [noteProblem, setNoteProblem] = useState<
    'reason_required' | 'reason_too_long' | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [incomplete, setIncomplete] = useState(false);
  const [unchanged, setUnchanged] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  // Entering an edit mode moves focus to the step heading (#305 F2).
  const moved = useRef(editing);

  const ready = attendance.status === 'ready' ? attendance.attendance : null;
  // A correction's attendance is read-only (#305 A4): nothing to complete.
  const missing = ready && mode !== 'correction' ? unmarkedNames(ready) : [];
  const absent = ready ? allAbsent(ready) : closeout.attendance.all_absent;
  const clientErrors = summaryErrors(summary, absent);
  const fieldErrors: SummaryErrors = { ...serverErrors, ...clientErrors };
  const minutesProblem = minutesError(minutes);
  const dirty =
    SUMMARY_FIELDS.some(
      (field) => summary[field].trim() !== baseline.summary[field].trim(),
    ) ||
    minutes.trim() !== baseline.minutes ||
    note.trim() !== '';

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    // Announce the new step to screen readers (not on the first render of
    // a new closeout).
    if (moved.current) {
      heading.current?.focus();
    }
    moved.current = true;
  }, [step]);

  function go(next: Step) {
    setFailure(null);
    setUnchanged(false);
    setStep(next);
  }

  function cancel() {
    if (dirty) {
      setConfirmCancel(true);
    } else {
      onCancel?.();
    }
  }

  function nextFromTime() {
    setMinutesTouched(true);
    if (minutesProblem === null) {
      go(3);
    }
  }

  function nextFromSummary() {
    setSummaryTouched(true);
    if (Object.keys(clientErrors).length === 0) {
      go(4);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    const submission: CloseoutSubmission = {
      actual_minutes: Number(minutes.trim()),
      summary: summaryBody(summary),
    };
    if (editing) {
      const problem = noteError(note, mode === 'correction');
      setNoteProblem(problem);
      if (problem) {
        noteField.current?.focus();
        return;
      }
      if (note.trim() !== '') {
        submission.reason = note.trim();
      }
    }
    setBusy(true);
    setFailure(null);
    setIncomplete(false);
    setUnchanged(false);
    try {
      if (editing) {
        await onResubmit?.(submission, mode);
      } else {
        await onSubmit(submission);
      }
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.code === 'closeout_attendance_incomplete') {
          setIncomplete(true);
          onReload();
          go(1);
          return;
        }
        if (caught.code === 'closeout_already_submitted') {
          onReload();
          return;
        }
        if (caught.code === 'closeout_unchanged') {
          setUnchanged(true);
          return;
        }
        const reasonField = caught.fieldErrors.find(
          (item) => item.path[1] === 'reason',
        );
        if (editing && reasonField) {
          setNoteProblem(
            reasonField.code === 'reason_too_long'
              ? 'reason_too_long'
              : 'reason_required',
          );
          noteField.current?.focus();
          return;
        }
        if (caught.status === 422) {
          const mapped = serverSummaryErrors(caught);
          if (Object.keys(mapped).length > 0) {
            setServerErrors(mapped);
            setSummaryTouched(true);
            go(3);
            return;
          }
        }
      }
      setFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  const conflict =
    failure instanceof ApiError && failure.code === 'version_conflict';
  const minutesId = `${id}-minutes`;
  const noteId = `${id}-note`;
  const noteLength = note.trim().length;
  const cancelButton = editing ? (
    <Button type="button" variant="outline" disabled={busy} onClick={cancel}>
      {t('closeout.tutorView.cancel')}
    </Button>
  ) : null;
  let submitLabel = t('closeout.submit');
  let submittingLabel = t('closeout.submitting');
  if (mode === 'resubmit') {
    submitLabel = t('closeout.tutorView.resubmitSubmit');
    submittingLabel = t('closeout.tutorView.resubmitting');
  } else if (mode === 'correction') {
    submitLabel = t('closeout.tutorView.correctionSubmit');
    submittingLabel = t('closeout.tutorView.correctionSubmitting');
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Stepper step={step} />
      <h3
        ref={heading}
        tabIndex={-1}
        className="text-base font-semibold outline-none"
      >
        {t('closeout.stepOf', { step, total: STEPS.length })}:{' '}
        {t(STEP_KEYS[step])}
      </h3>
      {conflict ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-destructive p-3 text-sm"
        >
          <p className="font-medium">{t('closeout.conflict')}</p>
          <div>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setFailure(null);
                onReload();
              }}
            >
              {t('attendance.reload')}
            </Button>
          </div>
        </div>
      ) : failure ? (
        <ErrorAlert error={failure} />
      ) : null}

      {step === 1 ? (
        <div className="flex min-w-0 flex-col gap-4">
          {incomplete ? (
            <p role="alert" className="text-sm font-medium">
              {t('closeout.attendanceIncomplete')}
            </p>
          ) : null}
          <AttendanceView
            state={attendance}
            timeZone={timeZone}
            onSave={onSaveAttendance}
            onReload={onReload}
            showHeader={false}
          />
          {missing.length > 0 ? (
            <div className="text-sm" id={`${id}-missing`}>
              <p className="font-medium">{t('closeout.missing')}</p>
              <ul className="list-disc pl-5">
                {missing.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {cancelButton}
            <Button
              type="button"
              disabled={!ready || missing.length > 0}
              aria-describedby={
                missing.length > 0 ? `${id}-missing` : undefined
              }
              onClick={() => go(2)}
            >
              {t('closeout.next')}
            </Button>
          </div>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex max-w-xs flex-col gap-2">
            <label htmlFor={minutesId} className="text-sm font-medium">
              {t('closeout.actualMinutes')}
            </label>
            <Input
              id={minutesId}
              type="number"
              inputMode="numeric"
              min={MINUTES_MIN}
              max={MINUTES_MAX}
              step={1}
              value={minutes}
              aria-invalid={minutesTouched && minutesProblem ? true : undefined}
              aria-describedby={`${minutesId}-hint ${minutesId}-error`}
              onChange={(event) => {
                setMinutes(event.target.value);
                setMinutesTouched(true);
              }}
            />
            <p
              id={`${minutesId}-hint`}
              className="text-sm text-muted-foreground"
            >
              {t('closeout.scheduled', {
                minutes: closeout.scheduled_minutes,
              })}
            </p>
            <FieldError
              id={`${minutesId}-error`}
              message={
                minutesTouched && minutesProblem
                  ? t('closeout.minutesRange', {
                      min: MINUTES_MIN,
                      max: MINUTES_MAX,
                    })
                  : undefined
              }
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {cancelButton}
            <Button type="button" variant="outline" onClick={() => go(1)}>
              {t('closeout.back')}
            </Button>
            <Button type="button" onClick={nextFromTime}>
              {t('closeout.next')}
            </Button>
          </div>
        </div>
      ) : null}

      {step === 3 ? (
        <div className="flex min-w-0 flex-col gap-4">
          {absent ? <p className="text-sm">{t('closeout.allAbsent')}</p> : null}
          {SUMMARY_FIELDS.map((field) => {
            const fieldId = `${id}-${field}`;
            const value = summary[field];
            const code = summaryTouched ? fieldErrors[field] : undefined;
            const required = isRequired(field, absent);
            const length = value.trim().length;
            return (
              <div key={field} className="flex min-w-0 flex-col gap-2">
                <label htmlFor={fieldId} className="text-sm font-medium">
                  {t(FIELD_KEYS[field])}{' '}
                  <span className="font-normal text-muted-foreground">
                    {required ? t('closeout.required') : t('closeout.optional')}
                  </span>
                </label>
                <textarea
                  id={fieldId}
                  rows={3}
                  value={value}
                  required={required}
                  aria-required={required}
                  aria-invalid={code ? true : undefined}
                  aria-describedby={`${fieldId}-error ${fieldId}-counter`}
                  onChange={(event) => {
                    setSummary((current) => ({
                      ...current,
                      [field]: event.target.value,
                    }));
                    setServerErrors((current) => {
                      const rest = { ...current };
                      delete rest[field];
                      return rest;
                    });
                  }}
                  className={TEXTAREA_CLASS}
                />
                <FieldError
                  id={`${fieldId}-error`}
                  message={
                    code
                      ? t(`closeout.errors.${code}`, { max: SUMMARY_MAX })
                      : undefined
                  }
                />
                <p
                  id={`${fieldId}-counter`}
                  className={
                    length > SUMMARY_MAX
                      ? 'text-sm text-destructive'
                      : 'text-sm text-muted-foreground'
                  }
                >
                  {t('scheduling.substitution.counter', {
                    length,
                    max: SUMMARY_MAX,
                  })}
                </p>
              </div>
            );
          })}
          <div className="flex flex-wrap gap-2">
            {cancelButton}
            <Button type="button" variant="outline" onClick={() => go(2)}>
              {t('closeout.back')}
            </Button>
            <Button type="button" onClick={nextFromSummary}>
              {t('closeout.next')}
            </Button>
          </div>
        </div>
      ) : null}

      {step === 4 ? (
        <form
          noValidate
          onSubmit={(event) => void submit(event)}
          className="flex min-w-0 flex-col gap-4"
        >
          {unchanged ? (
            <p
              role="alert"
              className="rounded-md border border-destructive p-3 text-sm font-medium"
            >
              {t('closeout.tutorView.unchanged')}
            </p>
          ) : null}
          <ReviewSection
            title={t('closeout.steps.attendance')}
            onEdit={() => go(1)}
            editLabel={t('closeout.editStep', {
              step: t('closeout.steps.attendance'),
            })}
          >
            {ready ? (
              <ul className="flex flex-col gap-1 text-sm">
                {ready.items.map((item) => (
                  <li
                    key={item.student.id}
                    className="flex flex-wrap items-center gap-2"
                  >
                    <span className="break-words">
                      {item.student.display_name}:
                    </span>
                    <StatusLabel status={item.status} />
                  </li>
                ))}
              </ul>
            ) : null}
          </ReviewSection>
          <ReviewSection
            title={t('closeout.steps.time')}
            onEdit={() => go(2)}
            editLabel={t('closeout.editStep', {
              step: t('closeout.steps.time'),
            })}
          >
            <p className="text-sm">
              {t('closeout.minutesOfScheduled', {
                actual: Number(minutes.trim()),
                scheduled: closeout.scheduled_minutes,
              })}
            </p>
          </ReviewSection>
          <ReviewSection
            title={t('closeout.steps.summary')}
            onEdit={() => go(3)}
            editLabel={t('closeout.editStep', {
              step: t('closeout.steps.summary'),
            })}
          >
            <SummaryList
              summary={{
                topics: summary.topics.trim() || null,
                performance: summary.performance.trim() || null,
                homework: summary.homework.trim() || null,
                next_steps: summary.next_steps.trim() || null,
              }}
            />
          </ReviewSection>
          {editing ? (
            <div className="flex min-w-0 flex-col gap-2">
              <label htmlFor={noteId} className="text-sm font-medium">
                {mode === 'correction'
                  ? t('closeout.tutorView.reason')
                  : t('closeout.tutorView.note')}{' '}
                <span className="font-normal text-muted-foreground">
                  {mode === 'correction'
                    ? t('closeout.required')
                    : t('closeout.optional')}
                </span>
              </label>
              <p
                id={`${noteId}-hint`}
                className="text-sm text-muted-foreground"
              >
                {mode === 'correction'
                  ? t('closeout.tutorView.reasonHint')
                  : t('closeout.tutorView.noteHint')}
              </p>
              <textarea
                id={noteId}
                ref={noteField}
                rows={3}
                value={note}
                required={mode === 'correction'}
                aria-required={mode === 'correction'}
                aria-invalid={noteProblem ? true : undefined}
                aria-describedby={[
                  `${noteId}-hint`,
                  noteProblem ? `${noteId}-error` : undefined,
                  `${noteId}-counter`,
                ]
                  .filter(Boolean)
                  .join(' ')}
                onChange={(event) => {
                  setNote(event.target.value);
                  setNoteProblem(null);
                }}
                className={TEXTAREA_CLASS}
              />
              <FieldError
                id={`${noteId}-error`}
                message={
                  noteProblem === 'reason_too_long'
                    ? t('closeout.tutorView.reasonTooLong')
                    : noteProblem
                      ? t('closeout.tutorView.reasonRequired')
                      : undefined
                }
              />
              <p
                id={`${noteId}-counter`}
                className={
                  noteLength > NOTE_MAX
                    ? 'text-sm text-destructive'
                    : 'text-sm text-muted-foreground'
                }
              >
                {t('scheduling.substitution.counter', {
                  length: noteLength,
                  max: NOTE_MAX,
                })}
              </p>
            </div>
          ) : null}
          <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap gap-2 border-t border-border bg-background px-4 py-3">
            {cancelButton}
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => go(3)}
            >
              {t('closeout.back')}
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? (
                <Spinner label={submittingLabel} showLabel />
              ) : (
                submitLabel
              )}
            </Button>
          </div>
        </form>
      ) : null}
      {editing ? (
        <Dialog open={confirmCancel} onOpenChange={setConfirmCancel}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('closeout.leave.title')}</DialogTitle>
              <DialogDescription>
                {t('closeout.leave.description')}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirmCancel(false)}
              >
                {t('closeout.leave.stay')}
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={() => {
                  setConfirmCancel(false);
                  onCancel?.();
                }}
              >
                {t('closeout.leave.leave')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}

const TEXTAREA_CLASS =
  'w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30';

function ReviewSection({
  title,
  onEdit,
  editLabel,
  children,
}: {
  title: string;
  onEdit: () => void;
  editLabel: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{title}</h4>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={editLabel}
          onClick={onEdit}
        >
          {t('closeout.edit')}
        </Button>
      </div>
      {children}
    </section>
  );
}
