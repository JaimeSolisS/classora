import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
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
import { Input } from '@/components/ui/input';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { useIdempotentSubmission } from '@/lib/idempotency';
import type { LocalDate } from '@/lib/time';
import type { PayItemCreate, PayItemRecurrence, PayItemType } from './api';
import {
  emptyPayItemForm,
  isStaleType,
  PAY_ITEM_RECURRENCES,
  payItemCreateBody,
  payItemServerErrors,
  validatePayItemForm,
  type PayItemErrors,
  type PayItemField,
  type PayItemFormValues,
} from './payItemForm';
import { INPUT_CLASS, SELECT_CLASS, SHEET_CLASS } from './sheet';

// Add a bonus or a deduction (#454 B3, B4), without any API call. The body is
// mounted once per opening, so its idempotent submission is too: pressing Add
// again with the same body resends the key, so a retry after a lost response
// can't pay twice, and a new opening starts with a new key. A changed body is a
// new submission, as everywhere (lib/idempotency): the server refuses one key
// with two bodies. Only active types are offered. A type deactivated meanwhile
// is refused on the field and the list refetched, which drops it; with none
// left the dialog links to the types page. After an answer that doesn't settle
// it, the alert says the item may be stored: only a resend from this opening is
// safe, because the key belongs to it. Closing refreshes the list, which may
// fail too, so the words promise nothing about it (QA #454 D4).

export type PayItemTypeOptions =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; types: readonly PayItemType[] };

export interface PayItemDialogProps {
  open: boolean;
  types: PayItemTypeOptions;
  /** Today's date in the center's zone, where the date starts. */
  today: LocalDate;
  /** Rejects with the error to show. */
  onSubmit: (body: PayItemCreate, idempotencyKey: string) => Promise<void>;
  /** Refetches the types after a refusal about the chosen one. */
  onReloadTypes: () => Promise<void>;
  /** The dialog closed: `saved` after a stored item. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the errors to start from. */
  initialValues?: Partial<PayItemFormValues>;
  initialErrors?: PayItemErrors;
}

/** No answer, or a server failure: the item may have been stored. */
function outcomeUnknown(failure: unknown): boolean {
  if (!failure) {
    return false;
  }
  return !(failure instanceof ApiError) || (failure.status ?? 500) >= 500;
}

export function PayItemDialog(props: PayItemDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose(false);
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const FIELD_ORDER: readonly PayItemField[] = [
  'typeId',
  'description',
  'amount',
  'recurrence',
  'effectiveFrom',
  'effectiveTo',
];

function DialogBody({
  types,
  today,
  onSubmit,
  onReloadTypes,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: PayItemDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [values, setValues] = useState<PayItemFormValues>(() => ({
    ...emptyPayItemForm(today),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<PayItemErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const active =
    types.status === 'ready'
      ? types.types.filter((type) => type.is_active)
      : [];
  if (
    !submitting &&
    types.status === 'ready' &&
    values.typeId !== '' &&
    !active.some((type) => type.id === values.typeId)
  ) {
    // The chosen type left the list: nothing may send it by accident.
    setValues({ ...values, typeId: '' });
  }

  const fieldId = (field: PayItemField) => `${id}-${field}`;
  const errorId = (field: PayItemField) => `${id}-${field}-error`;
  const ids = {
    descriptionHelp: `${id}-description-help`,
    recurringHelp: `${id}-recurring-help`,
    noTypes: `${id}-no-types`,
  };
  const noTypes = types.status === 'ready' && active.length === 0;
  const describedBy = (field: PayItemField, help?: string) =>
    [help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;

  function change(next: Partial<PayItemFormValues>) {
    setValues({ ...values, ...next });
  }

  function focusFirst(found: PayItemErrors) {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function submit() {
    const found = validatePayItemForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    const body = payItemCreateBody(values);
    // Outside the call (#88): a retry of the same body resends this key.
    const key = submission.keyFor(body);
    setFailure(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(body, key);
      submission.succeeded();
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = payItemServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
      }
      if (isStaleType(caught)) {
        try {
          await onReloadTypes();
        } catch {
          // The refusal on the field already says what happened.
        }
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) {
      return;
    }
    void submit();
  }

  const recurring = values.recurrence === 'recurring';

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('payroll.payItems.dialog.title')}</DialogTitle>
        <DialogDescription>
          {t('payroll.payItems.dialog.help')}
        </DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          detail={
            outcomeUnknown(failure) ? (
              <span data-pay-item-unknown>
                {t('payroll.payItems.dialog.outcomeUnknown')}
              </span>
            ) : undefined
          }
        />
      ) : null}
      {types.status === 'error' ? (
        <ErrorAlert error={types.error} onRetry={types.onRetry} />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('typeId')} className="text-sm font-medium">
            {t('payroll.payItems.dialog.type')}
          </label>
          <select
            id={fieldId('typeId')}
            className={SELECT_CLASS}
            value={values.typeId}
            disabled={submitting || active.length === 0}
            aria-invalid={errors.typeId ? true : undefined}
            aria-describedby={describedBy(
              'typeId',
              noTypes ? ids.noTypes : undefined,
            )}
            onChange={(event) => change({ typeId: event.target.value })}
          >
            <option value="">
              {types.status === 'loading'
                ? t('payroll.payItems.dialog.typesLoading')
                : t('payroll.payItems.dialog.chooseType')}
            </option>
            {active.map((type) => (
              <option key={type.id} value={type.id}>
                {`${type.name} (${t(`payroll.payItems.directions.${type.direction}`)})`}
              </option>
            ))}
          </select>
          {noTypes ? (
            <p id={ids.noTypes} className="text-sm" data-pay-item-no-types>
              {t('payroll.payItems.dialog.noTypes')}{' '}
              <Link
                to="/payroll/pay-item-types"
                className="inline-flex min-h-11 items-center font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6"
              >
                {t('payroll.payItems.dialog.typesLink')}
              </Link>
            </p>
          ) : null}
          <FieldError id={errorId('typeId')} message={message(errors.typeId)} />
        </div>
        <div className="flex flex-col gap-2">
          <label
            htmlFor={fieldId('description')}
            className="text-sm font-medium"
          >
            {t('payroll.payItems.dialog.description')}
          </label>
          <textarea
            id={fieldId('description')}
            rows={2}
            className={TEXTAREA_CLASS}
            value={values.description}
            disabled={submitting}
            aria-invalid={errors.description ? true : undefined}
            aria-describedby={describedBy('description', ids.descriptionHelp)}
            onChange={(event) => change({ description: event.target.value })}
          />
          <p id={ids.descriptionHelp} className="text-sm text-muted-foreground">
            {t('payroll.payItems.dialog.descriptionHelp')}
          </p>
          <FieldError
            id={errorId('description')}
            message={message(errors.description)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('amount')} className="text-sm font-medium">
            {t('payroll.payItems.dialog.amount')}
          </label>
          <Input
            id={fieldId('amount')}
            inputMode="decimal"
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.amount}
            disabled={submitting}
            aria-invalid={errors.amount ? true : undefined}
            aria-describedby={describedBy('amount')}
            onChange={(event) => change({ amount: event.target.value })}
          />
          <FieldError id={errorId('amount')} message={message(errors.amount)} />
        </div>
        <div className="flex flex-col gap-2">
          <label
            htmlFor={fieldId('recurrence')}
            className="text-sm font-medium"
          >
            {t('payroll.payItems.dialog.recurrence')}
          </label>
          <select
            id={fieldId('recurrence')}
            className={SELECT_CLASS}
            value={values.recurrence}
            disabled={submitting}
            aria-invalid={errors.recurrence ? true : undefined}
            aria-describedby={describedBy(
              'recurrence',
              recurring ? ids.recurringHelp : undefined,
            )}
            onChange={(event) =>
              change({ recurrence: event.target.value as PayItemRecurrence })
            }
          >
            {PAY_ITEM_RECURRENCES.map((recurrence) => (
              <option key={recurrence} value={recurrence}>
                {t(`payroll.payItems.recurrences.${recurrence}`)}
              </option>
            ))}
          </select>
          {recurring ? (
            <p id={ids.recurringHelp} className="text-sm">
              {t('payroll.payItems.dialog.recurringHelp')}
            </p>
          ) : null}
          <FieldError
            id={errorId('recurrence')}
            message={message(errors.recurrence)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label
            htmlFor={fieldId('effectiveFrom')}
            className="text-sm font-medium"
          >
            {t('payroll.payItems.dialog.effectiveFrom')}
          </label>
          <Input
            id={fieldId('effectiveFrom')}
            type="date"
            className={INPUT_CLASS}
            value={values.effectiveFrom}
            disabled={submitting}
            aria-invalid={errors.effectiveFrom ? true : undefined}
            aria-describedby={describedBy('effectiveFrom')}
            onChange={(event) => change({ effectiveFrom: event.target.value })}
          />
          <FieldError
            id={errorId('effectiveFrom')}
            message={message(errors.effectiveFrom)}
          />
        </div>
        {recurring ? (
          <div className="flex flex-col gap-2">
            <label
              htmlFor={fieldId('effectiveTo')}
              className="text-sm font-medium"
            >
              {t('payroll.payItems.dialog.effectiveTo')}
            </label>
            <Input
              id={fieldId('effectiveTo')}
              type="date"
              min={values.effectiveFrom || undefined}
              className={INPUT_CLASS}
              value={values.effectiveTo}
              disabled={submitting}
              aria-invalid={errors.effectiveTo ? true : undefined}
              aria-describedby={describedBy('effectiveTo')}
              onChange={(event) => change({ effectiveTo: event.target.value })}
            />
            <FieldError
              id={errorId('effectiveTo')}
              message={message(errors.effectiveTo)}
            />
          </div>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
            onClick={() => onClose(false)}
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
            ) : (
              t('payroll.payItems.dialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
