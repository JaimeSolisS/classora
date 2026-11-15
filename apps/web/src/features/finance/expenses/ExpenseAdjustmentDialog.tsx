import { useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import { FieldError } from '@/features/auth/FieldError';
import { INPUT_CLASS, SHEET_CLASS } from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { ExpenseAdjustmentCreate, ExpenseDetail } from '../api';
import { FormField } from './FormField';
import { LockedDialog } from './LockedDialog';
import {
  ADJUST_DIRECTIONS,
  ADJUSTMENT_FIELD_ORDER,
  adjustmentBody,
  adjustmentServerErrors,
  emptyAdjustmentForm,
  exceededOpen,
  REASON_MAX,
  textLength,
  validateAdjustmentForm,
  validateReason,
  voidBody,
  type AdjustmentErrors,
  type AdjustmentField,
  type AdjustmentFormValues,
} from './rules';
import { useLockedSubmission, type LockedControl } from './useLockedSubmission';

// "Ajustar" (M7-12b D3) and "Anular" (E3, D4), without any call. An
// adjustment is an explicit "Aumentar" / "Reducir" choice and an amount with
// no sign, plus a required reason; a reduction goes down to what's paid at
// most. The void is the same dialog with the amount fixed at minus the net
// and a final-action warning. Both need the password (`finance.correction`):
// apiCall opens the password dialog and resends the same body and key. The
// lost-answer rule is E2 (a): useLockedSubmission.

export type AdjustmentMode = 'adjust' | 'void';

export interface ExpenseAdjustmentDialogProps {
  open: boolean;
  mode: AdjustmentMode;
  expense: Pick<
    ExpenseDetail,
    'adjusted_amount' | 'paid_amount' | 'open_amount' | 'payments'
  >;
  onSubmit: (body: ExpenseAdjustmentCreate, key: string) => Promise<void>;
  onSaved: () => void;
  onClose: (failed: AttemptFailure) => void;
  initialValues?: Partial<AdjustmentFormValues>;
}

export function ExpenseAdjustmentDialog(props: ExpenseAdjustmentDialogProps) {
  return (
    <LockedDialog
      open={props.open}
      onClose={props.onClose}
      body={Body}
      bodyProps={props}
    />
  );
}

const D = 'finance.expenses.dialog';

function Body({
  mode,
  expense,
  onSubmit,
  onSaved,
  initialValues,
  busyRef,
  failedRef,
  onCancel,
}: ExpenseAdjustmentDialogProps & LockedControl) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const labels = useAccountLabels();
  const id = useId();
  const submission = useLockedSubmission<ExpenseAdjustmentCreate>(
    busyRef,
    failedRef,
  );
  const [values, setValues] = useState<AdjustmentFormValues>(() => ({
    ...emptyAdjustmentForm(),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<AdjustmentErrors>({});
  const [alertText, setAlertText] = useState<string | null>(null);
  const fieldId = (field: AdjustmentField) => `${id}-${field}`;
  const locked = submission.locked;
  const W = mode === 'void' ? `${D}.void` : `${D}.adjust`;

  function change(key: AdjustmentField, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  function focusFirst(found: AdjustmentErrors) {
    const field = ADJUSTMENT_FIELD_ORDER.find((name) => found[name]);
    if (field === 'direction') {
      document.getElementById(`${id}-direction-increase`)?.focus();
    } else if (field) {
      document.getElementById(fieldId(field))?.focus();
    }
  }

  function build(): ExpenseAdjustmentCreate | null {
    if (mode === 'void') {
      const reason = validateReason(values.reason);
      const found: AdjustmentErrors = reason ? { reason } : {};
      setErrors(found);
      if (reason) {
        focusFirst(found);
        return null;
      }
      return voidBody(expense, values.reason);
    }
    const found = validateAdjustmentForm(values, expense);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return null;
    }
    return adjustmentBody(values);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAlertText(null);
    void submission.submit({
      build,
      send: onSubmit,
      onSaved,
      onRefused: (caught) => {
        const found = adjustmentServerErrors(caught);
        if (found) {
          // The void's amount isn't a field: its refusal goes on the alert.
          if (mode === 'void' && found.amount && !found.reason) return false;
          setErrors(found);
          focusFirst(found);
          return true;
        }
        const open = exceededOpen(caught);
        if (open) {
          setAlertText(
            t(`${D}.adjust.exceedsOpen`, { open: labels.money(open) }),
          );
        }
        return false;
      },
    });
  }

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-expense-dialog={mode}
    >
      <DialogHeader>
        <DialogTitle>{t(`${W}.title`)}</DialogTitle>
        <DialogDescription>{t(`${W}.help`)}</DialogDescription>
      </DialogHeader>
      {submission.failure ? (
        <ErrorAlert
          error={submission.failure}
          message={submission.lost ? t(`${W}.lost`) : (alertText ?? undefined)}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {mode === 'void' ? (
          <p className="text-sm font-medium" data-void-amount>
            {t(`${D}.void.amount`, {
              amount: labels.money(expense.adjusted_amount),
            })}
          </p>
        ) : (
          <>
            <p className="text-sm" data-adjust-figures>
              {t(`${D}.adjust.figures`, {
                net: labels.money(expense.adjusted_amount),
                paid: labels.money(expense.paid_amount),
                open: labels.money(expense.open_amount),
              })}
            </p>
            <fieldset
              className="flex min-w-0 flex-col gap-2"
              aria-describedby={
                errors.direction ? `${id}-direction-error` : undefined
              }
            >
              <legend className="mb-2 text-sm font-medium">
                {t(`${D}.adjust.direction`)}
              </legend>
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                {ADJUST_DIRECTIONS.map((direction) => (
                  <label
                    key={direction}
                    className="flex min-h-11 items-center gap-2 text-sm md:min-h-6"
                  >
                    <input
                      id={`${id}-direction-${direction}`}
                      type="radio"
                      name={`${id}-direction`}
                      value={direction}
                      className="size-4 accent-primary"
                      checked={values.direction === direction}
                      disabled={locked}
                      onChange={() => change('direction', direction)}
                    />
                    {t(`${D}.adjust.${direction}`)}
                  </label>
                ))}
              </div>
              <FieldError
                id={`${id}-direction-error`}
                message={message(errors.direction)}
              />
            </fieldset>
            <FormField
              id={fieldId('amount')}
              label={t(`${D}.adjust.amount`)}
              hint={t(`${D}.amountHint`)}
              error={message(errors.amount)}
            >
              {(field) => (
                <Input
                  {...field}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  className={INPUT_CLASS}
                  value={values.amount}
                  disabled={locked}
                  onChange={(event) => change('amount', event.target.value)}
                />
              )}
            </FormField>
          </>
        )}
        <FormField
          id={fieldId('reason')}
          label={t(`${W}.reason`)}
          counter={t(`${D}.counter`, {
            n: textLength(values.reason),
            max: REASON_MAX,
          })}
          error={message(errors.reason)}
        >
          {(field) => (
            <textarea
              {...field}
              rows={3}
              className={TEXTAREA_CLASS}
              value={values.reason}
              disabled={locked}
              onChange={(event) => change('reason', event.target.value)}
            />
          )}
        </FormField>
        <p className="text-sm text-muted-foreground">
          {t(`${D}.adjust.password`)}
        </p>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submission.submitting}
            onClick={onCancel}
          >
            {t('finance.cancel')}
          </Button>
          <Button
            type="submit"
            variant={mode === 'void' ? 'destructive' : 'default'}
            className="min-h-11 md:min-h-9"
            disabled={submission.submitting}
          >
            {submission.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : submission.lost ? (
              t(`${W}.resend`)
            ) : (
              t(`${W}.submit`)
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
