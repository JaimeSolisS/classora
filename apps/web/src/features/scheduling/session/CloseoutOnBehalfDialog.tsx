import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { Spinner } from '@/components/feedback/Spinner';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
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
import type { SessionCloseoutRecord } from '@/features/tutor-portal/closeout/api';
import {
  EMPTY_SUMMARY,
  SUMMARY_FIELDS,
  minutesError,
  serverSummaryErrors,
  summaryBody,
  summaryDraftOf,
  summaryErrors,
  type SummaryDraft,
  type SummaryErrors,
  type SummaryField,
} from '@/features/tutor-portal/closeout/rules';
import { CloseoutFields } from './CloseoutFields';
import {
  missingStudentsOf,
  onBehalfNeedsReload,
  reasonError,
} from './closeoutReview';
import { useFocusAfterSend } from './useFocusAfterSend';
import { REVIEW_TEXTAREA_CLASS } from './ReviewFailure';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// "Close out on behalf of {tutor}" and "Take over and resubmit" (#301b C2,
// D2): a reviewer enters the actual minutes and the summary (shared with
// "Correct" through CloseoutFields; #77 E4 required marks unless everyone
// was absent) and a required reason (1-1000 characters once trimmed, with a
// counter). The attendance line says how many students are marked; while
// any is unmarked it names them (from the page's attendance, never the
// closeout read) and Submit sends nothing: focus moves to that line. Staff
// mark the rest with the attendance section's "Correct" first. The submit
// mode starts from the scheduled minutes and an empty summary; the takeover
// mode from the closeout. The section adds the version (the session's for
// the submit, the closeout's for the takeover).

export type OnBehalfMode = 'submit' | 'takeover';

export interface OnBehalfValues {
  actual_minutes: number;
  summary: Partial<Record<SummaryField, string>>;
  reason: string;
}

export interface CloseoutOnBehalfDialogProps {
  open: boolean;
  mode: OnBehalfMode;
  /** The tutor of record (the closeout's or the session's tutor). */
  tutorName: string;
  scheduledMinutes: number;
  allAbsent: boolean;
  attendance: { marked: number; total: number };
  /** The page's attendance rows, for the unmarked students' names. */
  students: readonly {
    student: { id: string; display_name: string };
    status: string | null;
  }[];
  /** The closeout to take over (prefills the takeover mode). */
  record?: SessionCloseoutRecord | null;
  onConfirm: (values: OnBehalfValues) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories. */
  initialError?: unknown;
}

export function CloseoutOnBehalfDialog(props: CloseoutOnBehalfDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          props.onCancel();
        }
      }}
    >
      {props.open ? <OnBehalfContent {...props} sendingRef={sending} /> : null}
    </Dialog>
  );
}

function OnBehalfContent({
  mode,
  tutorName,
  scheduledMinutes,
  allAbsent,
  attendance,
  students,
  record = null,
  onConfirm,
  onCancel,
  onReload,
  initialError = null,
  sendingRef,
}: CloseoutOnBehalfDialogProps & { sendingRef: { current: boolean } }) {
  const { t } = useTranslation();
  const id = useId();
  const reasonId = `${id}-reason`;
  const first = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const completeness = useRef<HTMLParagraphElement>(null);
  const returnFocus = useRef<Element | null>(null);
  const takeover = mode === 'takeover';
  const [minutes, setMinutes] = useState(
    String(
      takeover && record
        ? (record.actual_minutes ?? scheduledMinutes)
        : scheduledMinutes,
    ),
  );
  const [draft, setDraft] = useState<SummaryDraft>(
    takeover && record ? summaryDraftOf(record.summary) : EMPTY_SUMMARY,
  );
  const [reason, setReason] = useState('');
  const [checked, setChecked] = useState(false);
  const [serverErrors, setServerErrors] = useState<SummaryErrors>({});
  const [serverMinutes, setServerMinutes] = useState(false);
  const [serverReason, setServerReason] = useState<string | null>(null);
  const [serverMissing, setServerMissing] = useState<{
    names: string[];
    unknown: number;
  } | null>(null);
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);
  const focusAfterSend = useFocusAfterSend(busy);

  const minutesProblem = minutesError(minutes);
  const fieldErrors: SummaryErrors = {
    ...summaryErrors(draft, allAbsent),
    ...serverErrors,
  };
  const shownErrors: SummaryErrors = {};
  for (const field of SUMMARY_FIELDS) {
    const code = fieldErrors[field];
    if (code && (checked || serverErrors[field])) {
      shownErrors[field] = code;
    }
  }
  const reasonProblem = serverReason ?? reasonError(reason);
  const reasonLengthNow = reasonLength(reason);

  // The unmarked students: the server's list after a 409, else the page's.
  const localNames = students
    .filter((item) => item.status === null)
    .map((item) => item.student.display_name);
  const missing = serverMissing ?? {
    names: localNames,
    unknown: Math.max(
      0,
      attendance.total - attendance.marked - localNames.length,
    ),
  };
  const incomplete =
    serverMissing !== null || attendance.marked < attendance.total;
  const marked = serverMissing
    ? Math.max(
        0,
        attendance.total - serverMissing.names.length - serverMissing.unknown,
      )
    : attendance.marked;

  function reasonMessage(code: string): string {
    return code === 'reason_too_long'
      ? t('closeout.review.reasonTooLong')
      : t('closeout.review.reasonRequired');
  }

  function focusFirstInvalid(
    summary: SummaryErrors,
    onMinutes: boolean,
    onReason: boolean,
  ) {
    if (onMinutes) {
      first.current?.focus();
      return;
    }
    const field = SUMMARY_FIELDS.find((name) => summary[name]);
    if (field) {
      document.getElementById(`${id}-${field}`)?.focus();
      return;
    }
    if (onReason) {
      reasonRef.current?.focus();
    }
  }

  async function send() {
    setChecked(true);
    setFailure(null);
    if (incomplete) {
      completeness.current?.focus();
      return;
    }
    const local = summaryErrors(draft, allAbsent);
    if (
      minutesProblem ||
      Object.keys(local).length > 0 ||
      reasonError(reason)
    ) {
      focusFirstInvalid(
        local,
        minutesProblem !== null,
        reasonError(reason) !== null,
      );
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm({
        actual_minutes: Number(minutes.trim()),
        summary: summaryBody(draft),
        reason: reason.trim(),
      });
    } catch (caught) {
      const summary = serverSummaryErrors(caught);
      const fields = caught instanceof ApiError ? caught.fieldErrors : [];
      const onReason = fields.find(({ path }) => path.at(-1) === 'reason');
      const onMinutes = fields.some(
        ({ path }) => path.at(-1) === 'actual_minutes',
      );
      const missingNow = missingStudentsOf(caught, students);
      if (missingNow) {
        setServerMissing(missingNow);
        focusAfterSend(() => completeness.current?.focus());
      } else if (Object.keys(summary).length > 0 || onReason || onMinutes) {
        setServerErrors(summary);
        setServerMinutes(onMinutes);
        setServerReason(onReason ? onReason.code : null);
        focusAfterSend(() =>
          focusFirstInvalid(summary, onMinutes, Boolean(onReason)),
        );
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

  const title = takeover
    ? t('closeout.onBehalf.takeOver')
    : t('closeout.onBehalf.action', { tutor: tutorName });
  const unnamed =
    missing.unknown > 0
      ? t('closeout.onBehalf.moreStudents', { count: missing.unknown })
      : null;
  const missingList = [...missing.names, ...(unnamed ? [unnamed] : [])];

  return (
    <DialogContent
      onOpenAutoFocus={(event) => {
        returnFocus.current = document.activeElement;
        event.preventDefault();
        first.current?.focus();
      }}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        const target = returnFocus.current;
        if (target instanceof HTMLElement && target.isConnected) {
          target.focus();
        }
      }}
      onInteractOutside={(event) => event.preventDefault()}
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl"
    >
      <DialogHeader>
        <DialogTitle className="break-words">{title}</DialogTitle>
        <DialogDescription>
          {takeover
            ? t('closeout.onBehalf.takeOverExplain', { tutor: tutorName })
            : t('closeout.onBehalf.description', { tutor: tutorName })}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {failure ? (
          <ErrorAlert
            error={failure}
            onRetry={onBehalfNeedsReload(failure) ? onReload : undefined}
            retryLabel={t('closeout.review.reload')}
          />
        ) : null}
        <div
          className={
            incomplete
              ? 'flex min-w-0 flex-col gap-1 rounded-md border border-destructive p-3 text-sm'
              : 'flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm'
          }
        >
          <p
            ref={completeness}
            tabIndex={-1}
            role={incomplete && checked ? 'alert' : undefined}
            className="font-medium break-words outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('closeout.onBehalf.complete', {
              marked,
              total: attendance.total,
            })}
            {incomplete ? (
              <>
                {' '}
                {t('closeout.onBehalf.markRest')} {missingList.join(', ')}
              </>
            ) : null}
          </p>
        </div>
        <CloseoutFields
          id={id}
          minutes={minutes}
          onMinutes={(value) => {
            setMinutes(value);
            setServerMinutes(false);
          }}
          minutesInvalid={Boolean((checked && minutesProblem) || serverMinutes)}
          minutesRef={first}
          scheduledMinutes={scheduledMinutes}
          draft={draft}
          onField={(field, value) => {
            setDraft((current) => ({ ...current, [field]: value }));
            setServerErrors((current) => {
              const rest = { ...current };
              delete rest[field];
              return rest;
            });
          }}
          errors={shownErrors}
          allAbsent={allAbsent}
          disabled={busy}
        />
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('closeout.onBehalf.reason')}{' '}
            <span className="font-normal text-muted-foreground">
              {t('closeout.required')}
            </span>
          </label>
          <textarea
            id={reasonId}
            ref={reasonRef}
            rows={3}
            required
            aria-required
            value={reason}
            aria-invalid={checked && reasonProblem ? true : undefined}
            aria-describedby={`${reasonId}-error ${reasonId}-counter`}
            onChange={(event) => {
              setReason(event.target.value);
              setServerReason(null);
            }}
            className={REVIEW_TEXTAREA_CLASS}
          />
          <FieldError
            id={`${reasonId}-error`}
            message={
              checked && reasonProblem
                ? reasonMessage(reasonProblem)
                : undefined
            }
          />
          <p
            id={`${reasonId}-counter`}
            className={
              reasonLengthNow > REASON_MAX_LENGTH
                ? 'text-sm text-destructive'
                : 'text-sm text-muted-foreground'
            }
          >
            {t('scheduling.substitution.counter', {
              length: reasonLengthNow,
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
            {t('closeout.review.cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? (
              <Spinner label={t('closeout.onBehalf.submitting')} showLabel />
            ) : takeover ? (
              t('closeout.onBehalf.takeOverSubmit')
            ) : (
              t('closeout.onBehalf.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
