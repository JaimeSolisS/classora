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
import { Input } from '@/components/ui/input';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { useFormatters } from '@/lib/formatters';
import { useIdempotentSubmission } from '@/lib/idempotency';
import type { PayrollAdjustmentCreate } from './api';
import {
  ADJUSTMENT_DIRECTIONS,
  ADJUSTMENT_REASON_MAX,
  adjustmentCreateBody,
  adjustmentServerErrors,
  EMPTY_ADJUSTMENT_FORM,
  reasonLength,
  validateAdjustmentForm,
  type AdjustmentErrors,
  type AdjustmentField,
  type AdjustmentFormValues,
  type AdjustmentReference,
} from './adjustmentForm';
import { isCode, isUnknownResult } from './periodRules';
import { INPUT_CLASS, SHEET_CLASS } from './sheet';

// A manual adjustment (M7-08b B, E1), without any API call. Its reference is
// fixed by where it opened and shown read-only. The body is mounted once per
// opening, so is its idempotent submission: the key is computed before the
// call, so apiCall's retry after the password dialog (`finance.correction`)
// resends it, and so does pressing Save again after a lost answer.
//
// A lost answer (no response, or a 5xx) may have stored the adjustment. The
// dialog then locks the fields, says so, and offers only a resend of the same
// body with the same key: that one is safe, an edited one wouldn't be (the
// #454 and #459 lessons). A cancelled password dialog leaves it as it was.
// Settled refusals go on their field or in the alert.

export interface ManualAdjustmentDialogProps {
  open: boolean;
  tutorName: string;
  reference: AdjustmentReference;
  /** Posts; rejects with the error to show. */
  onSubmit: (body: PayrollAdjustmentCreate, key: string) => Promise<void>;
  /** `saved` after a stored adjustment; `unknown` after a lost answer. */
  onClose: (outcome: 'saved' | 'cancelled' | 'unknown') => void;
  /** Stories: the values to start from. */
  initialValues?: Partial<AdjustmentFormValues>;
}

export function ManualAdjustmentDialog(props: ManualAdjustmentDialogProps) {
  const busy = useRef(false);
  const unknown = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose(unknown.current ? 'unknown' : 'cancelled');
        }
      }}
    >
      {props.open ? (
        <Body {...props} busyRef={busy} unknownRef={unknown} />
      ) : null}
    </Dialog>
  );
}

const FIELD_ORDER: readonly AdjustmentField[] = [
  'direction',
  'amount',
  'reason',
];

function Body({
  tutorName,
  reference,
  onSubmit,
  onClose,
  initialValues,
  busyRef,
  unknownRef,
}: ManualAdjustmentDialogProps & {
  busyRef: { current: boolean };
  unknownRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [values, setValues] = useState<AdjustmentFormValues>(() => ({
    ...EMPTY_ADJUSTMENT_FORM,
    ...initialValues,
  }));
  const [errors, setErrors] = useState<AdjustmentErrors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  // A lost answer: only the same body may go again (see above).
  const [unknown, setUnknown] = useState(false);
  const D = 'payroll.adjustmentDialog';

  const fieldId = (field: AdjustmentField) => `${id}-${field}`;
  const errorId = (field: AdjustmentField) => `${id}-${field}-error`;
  const ids = {
    reasonHelp: `${id}-reason-help`,
    counter: `${id}-reason-counter`,
    reference: `${id}-reference`,
    directionLabel: `${id}-direction-label`,
  };
  const describedBy = (field: AdjustmentField, ...help: string[]) =>
    [...help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const locked = submitting || unknown;

  function change(next: Partial<AdjustmentFormValues>) {
    setValues({ ...values, ...next });
  }

  function focusFirst(found: AdjustmentErrors) {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first === 'direction') {
      document.getElementById(`${fieldId('direction')}-pay_more`)?.focus();
    } else if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function submit() {
    const found = validateAdjustmentForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    const body = adjustmentCreateBody(values, reference);
    // Outside the call (#88): a resend of this body resends this key.
    const key = submission.keyFor(body);
    setFailure(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(body, key);
      submission.succeeded();
      busyRef.current = false;
      unknownRef.current = false;
      onClose('saved');
      return;
    } catch (caught) {
      if (isCode(caught, 'reauth_required')) {
        // The password dialog was cancelled: as it was, no alert.
      } else if (isUnknownResult(caught)) {
        unknownRef.current = true;
        setUnknown(true);
        setFailure(caught);
      } else {
        const fromServer = adjustmentServerErrors(caught);
        if (fromServer) {
          setErrors(fromServer);
          focusFirst(fromServer);
        } else {
          setFailure(caught);
        }
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    void submit();
  }

  const length = reasonLength(values.reason);
  let referenceText: string;
  if (reference.kind === 'line') {
    referenceText = t(`${D}.referenceLine`, { line: reference.summary });
  } else if (reference.kind === 'session') {
    referenceText = t(`${D}.referenceSession`, {
      date: formatLocalDate(reference.serviceDate),
      reason: t(`payroll.unresolved.reasons.${reference.reason}`),
    });
  } else {
    referenceText = t(`${D}.referenceNone`);
  }

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-adjustment-dialog={reference.kind}
    >
      <DialogHeader>
        <DialogTitle>
          {t(`${D}.title.${reference.kind}`, { name: tutorName })}
        </DialogTitle>
        <DialogDescription>{t(`${D}.help`)}</DialogDescription>
      </DialogHeader>
      {reference.kind === 'free' ? (
        <div
          role="note"
          className="rounded-md border border-destructive p-3 text-sm"
          data-stranded-warning
        >
          <p className="font-medium">{t(`${D}.strandedTitle`)}</p>
          <p>{t(`${D}.stranded`)}</p>
        </div>
      ) : null}
      {failure ? (
        <ErrorAlert
          error={failure}
          message={isUnknownResult(failure) ? t(`${D}.unknown`) : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex min-w-0 flex-col gap-1" data-adjustment-reference>
          <p id={ids.reference} className="text-sm font-medium">
            {t(`${D}.reference`)}
          </p>
          <p className="text-sm break-words">{referenceText}</p>
        </div>
        <fieldset
          className="flex flex-col gap-2"
          aria-describedby={describedBy('direction')}
          aria-invalid={errors.direction ? true : undefined}
        >
          <legend id={ids.directionLabel} className="text-sm font-medium">
            {t(`${D}.direction`)}
          </legend>
          <div className="flex flex-wrap gap-4">
            {ADJUSTMENT_DIRECTIONS.map((direction) => (
              <label
                key={direction}
                className="flex min-h-11 items-center gap-2 text-sm md:min-h-8"
              >
                <input
                  id={`${fieldId('direction')}-${direction}`}
                  type="radio"
                  name={fieldId('direction')}
                  className="size-5"
                  value={direction}
                  checked={values.direction === direction}
                  disabled={locked}
                  onChange={() => change({ direction })}
                />
                {t(`${D}.directions.${direction}`)}
              </label>
            ))}
          </div>
          <FieldError
            id={errorId('direction')}
            message={message(errors.direction)}
          />
        </fieldset>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('amount')} className="text-sm font-medium">
            {t(`${D}.amount`)}
          </label>
          <Input
            id={fieldId('amount')}
            inputMode="decimal"
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.amount}
            disabled={locked}
            aria-invalid={errors.amount ? true : undefined}
            aria-describedby={describedBy('amount')}
            onChange={(event) => change({ amount: event.target.value })}
          />
          <FieldError id={errorId('amount')} message={message(errors.amount)} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('reason')} className="text-sm font-medium">
            {t(`${D}.reason`)}
          </label>
          <textarea
            id={fieldId('reason')}
            rows={3}
            className={TEXTAREA_CLASS}
            value={values.reason}
            disabled={locked}
            aria-invalid={errors.reason ? true : undefined}
            aria-describedby={describedBy(
              'reason',
              ids.reasonHelp,
              ids.counter,
            )}
            onChange={(event) => change({ reason: event.target.value })}
          />
          <p id={ids.reasonHelp} className="text-sm" data-reason-help>
            {t(`${D}.reasonHelp`)}
          </p>
          <p
            id={ids.counter}
            className="text-sm text-muted-foreground"
            data-reason-counter
          >
            {t(`${D}.counter`, { n: length, max: ADJUSTMENT_REASON_MAX })}
          </p>
          <FieldError id={errorId('reason')} message={message(errors.reason)} />
        </div>
        <p className="text-sm text-muted-foreground">{t(`${D}.target`)}</p>
        <p className="text-sm text-muted-foreground">{t(`${D}.password`)}</p>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
            onClick={() => onClose(unknown ? 'unknown' : 'cancelled')}
          >
            {t('payroll.cancel')}
          </Button>
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
          >
            {submitting ? (
              <Spinner label={t('payroll.saving')} showLabel />
            ) : unknown ? (
              t(`${D}.resend`)
            ) : (
              t(`${D}.submit`)
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
