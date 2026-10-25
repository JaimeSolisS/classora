import { useId, useRef, useState, type FormEvent } from 'react';
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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type {
  CloseoutCorrectionBody,
  SessionCloseoutRecord,
} from '@/features/tutor-portal/closeout/api';
import {
  MINUTES_MAX,
  MINUTES_MIN,
  SUMMARY_FIELDS,
  SUMMARY_MAX,
  isRequired,
  minutesError,
  serverSummaryErrors,
  summaryErrors,
  type SummaryDraft,
  type SummaryErrors,
} from '@/features/tutor-portal/closeout/rules';
import {
  correctionBody,
  isUnchanged,
  reasonError,
  summaryDraftOf,
} from './closeoutReview';
import { REVIEW_TEXTAREA_CLASS, ReviewFailure } from './ReviewFailure';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// "Correct" (#78 G1, E5): the actual minutes and the four summary fields,
// prefilled from the closeout, with the #77 E4 required marks (none when
// everyone was absent), and a required reason. Only the changed members are
// sent (a cleared field as null); nothing is sent while a field is invalid
// or nothing changed. On an approved closeout it says that saving creates a
// new approval. "Re-approve" (E6) opens it with the fields locked: only the
// reason is sent, approving the current attendance again. Focus starts on
// the first field (the reason when locked) and returns to the trigger.

const FIELD_KEYS = {
  topics: 'closeout.fields.topics',
  performance: 'closeout.fields.performance',
  homework: 'closeout.fields.homework',
  next_steps: 'closeout.fields.next_steps',
} as const;

export interface CorrectCloseoutDialogProps {
  open: boolean;
  record: SessionCloseoutRecord;
  scheduledMinutes: number;
  allAbsent: boolean;
  /** E6: fields locked, the reason only. */
  reapprove?: boolean;
  onConfirm: (body: CloseoutCorrectionBody) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories. */
  initialError?: unknown;
}

export function CorrectCloseoutDialog(props: CorrectCloseoutDialogProps) {
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
      {props.open ? <CorrectContent {...props} sendingRef={sending} /> : null}
    </Dialog>
  );
}

function CorrectContent({
  record,
  scheduledMinutes,
  allAbsent,
  reapprove = false,
  onConfirm,
  onCancel,
  onReload,
  initialError = null,
  sendingRef,
}: CorrectCloseoutDialogProps & { sendingRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const minutesId = `${id}-minutes`;
  const reasonId = `${id}-reason`;
  const first = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef<Element | null>(null);
  const [minutes, setMinutes] = useState(String(record.actual_minutes ?? 0));
  const [draft, setDraft] = useState<SummaryDraft>(
    summaryDraftOf(record.summary),
  );
  const [reason, setReason] = useState('');
  const [checked, setChecked] = useState(false);
  const [serverErrors, setServerErrors] = useState<SummaryErrors>({});
  const [serverMinutes, setServerMinutes] = useState(false);
  const [serverReason, setServerReason] = useState<string | null>(null);
  const [unchanged, setUnchanged] = useState(false);
  const [failure, setFailure] = useState<unknown>(initialError);
  const [busy, setBusy] = useState(false);

  const minutesProblem = reapprove ? null : minutesError(minutes);
  const fieldErrors: SummaryErrors = reapprove
    ? {}
    : { ...summaryErrors(draft, allAbsent), ...serverErrors };
  const reasonProblem = serverReason ?? reasonError(reason);

  function reasonMessage(code: string): string {
    return code === 'reason_too_long'
      ? t('closeout.review.reasonTooLong')
      : t('closeout.review.reasonRequired');
  }

  async function send() {
    setChecked(true);
    setFailure(null);
    setUnchanged(false);
    if (
      minutesProblem ||
      Object.keys(summaryErrors(draft, allAbsent)).length > 0
    ) {
      first.current?.focus();
      return;
    }
    if (reasonError(reason)) {
      reasonRef.current?.focus();
      return;
    }
    const body = correctionBody(record, minutes, draft, reason, { reapprove });
    if (!reapprove && isUnchanged(body)) {
      setUnchanged(true);
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    try {
      await onConfirm(body);
    } catch (caught) {
      const summary = serverSummaryErrors(caught);
      const fields = caught instanceof ApiError ? caught.fieldErrors : [];
      const onReason = fields.find(({ path }) => path.at(-1) === 'reason');
      const onMinutes = fields.some(
        ({ path }) => path.at(-1) === 'actual_minutes',
      );
      if (caught instanceof ApiError && caught.code === 'closeout_unchanged') {
        setUnchanged(true);
      } else if (Object.keys(summary).length > 0 || onReason || onMinutes) {
        setServerErrors(summary);
        setServerMinutes(onMinutes);
        setServerReason(onReason ? onReason.code : null);
        if (onReason) {
          reasonRef.current?.focus();
        }
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

  const reasonLengthNow = reasonLength(reason);
  const approved = record.status === 'approved';

  return (
    <DialogContent
      onOpenAutoFocus={(event) => {
        returnFocus.current = document.activeElement;
        event.preventDefault();
        (reapprove ? reasonRef.current : first.current)?.focus();
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
        <DialogTitle>
          {reapprove
            ? t('closeout.review.correctDialog.reapproveTitle')
            : t('closeout.review.correctDialog.title')}
        </DialogTitle>
        <DialogDescription>
          {reapprove
            ? t('closeout.review.correctDialog.reapproveDescription')
            : t('closeout.review.correctDialog.description')}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {approved && !reapprove ? (
          <p className="rounded-md border border-border p-3 text-sm font-medium">
            {t('closeout.review.correctDialog.approvedNote')}
          </p>
        ) : null}
        {unchanged ? (
          <p
            role="alert"
            className="rounded-md border border-destructive p-3 text-sm"
          >
            {tErrors('closeout_unchanged')}
          </p>
        ) : null}
        <ReviewFailure error={failure} onReload={onReload} />
        <div className="flex max-w-xs flex-col gap-2">
          <label htmlFor={minutesId} className="text-sm font-medium">
            {t('closeout.actualMinutes')}
          </label>
          <Input
            id={minutesId}
            ref={first}
            type="number"
            inputMode="numeric"
            min={MINUTES_MIN}
            max={MINUTES_MAX}
            step={1}
            value={minutes}
            readOnly={reapprove}
            disabled={busy}
            aria-invalid={
              (checked && minutesProblem) || serverMinutes ? true : undefined
            }
            aria-describedby={`${minutesId}-hint ${minutesId}-error`}
            onChange={(event) => {
              setMinutes(event.target.value);
              setServerMinutes(false);
            }}
          />
          <p id={`${minutesId}-hint`} className="text-sm text-muted-foreground">
            {t('closeout.scheduled', { minutes: scheduledMinutes })}
          </p>
          <FieldError
            id={`${minutesId}-error`}
            message={
              (checked && minutesProblem) || serverMinutes
                ? t('closeout.minutesRange', {
                    min: MINUTES_MIN,
                    max: MINUTES_MAX,
                  })
                : undefined
            }
          />
        </div>
        {allAbsent && !reapprove ? (
          <p className="text-sm">{t('closeout.allAbsent')}</p>
        ) : null}
        {SUMMARY_FIELDS.map((field) => {
          const fieldId = `${id}-${field}`;
          const value = draft[field];
          const code =
            checked || serverErrors[field] ? fieldErrors[field] : undefined;
          const required = !reapprove && isRequired(field, allAbsent);
          const length = value.trim().length;
          return (
            <div key={field} className="flex min-w-0 flex-col gap-2">
              <label htmlFor={fieldId} className="text-sm font-medium">
                {t(FIELD_KEYS[field])}{' '}
                {reapprove ? null : (
                  <span className="font-normal text-muted-foreground">
                    {required ? t('closeout.required') : t('closeout.optional')}
                  </span>
                )}
              </label>
              <textarea
                id={fieldId}
                rows={3}
                value={value}
                readOnly={reapprove}
                disabled={busy}
                required={required}
                aria-required={required}
                aria-invalid={code ? true : undefined}
                aria-describedby={`${fieldId}-error ${fieldId}-counter`}
                onChange={(event) => {
                  setDraft((current) => ({
                    ...current,
                    [field]: event.target.value,
                  }));
                  setServerErrors((current) => {
                    const rest = { ...current };
                    delete rest[field];
                    return rest;
                  });
                }}
                className={REVIEW_TEXTAREA_CLASS}
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
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('closeout.review.correctDialog.reason')}{' '}
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
            disabled={busy}
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
              <Spinner
                label={t('closeout.review.correctDialog.submitting')}
                showLabel
              />
            ) : reapprove ? (
              t('closeout.review.correctDialog.reapproveSubmit')
            ) : (
              t('closeout.review.correctDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
