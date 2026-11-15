import { useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import { isCode } from '@/features/payroll/periodRules';
import {
  INPUT_CLASS,
  SELECT_CLASS,
  SHEET_CLASS,
} from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { LocalDate } from '@/lib/time';
import type { ExpenseCategory, ExpenseCreate, Vendor } from '../api';
import { useCategoryLabel } from '../labels';
import { FormField } from './FormField';
import { LockedDialog } from './LockedDialog';
import {
  DESCRIPTION_MAX,
  emptyExpenseForm,
  EXPENSE_FIELD_ORDER,
  expenseCreateBody,
  expenseServerErrors,
  NOTE_MAX,
  RECEIPT_REFERENCE_MAX,
  RELOAD_PICKERS_AFTER,
  textLength,
  validateExpenseForm,
  type ExpenseErrors,
  type ExpenseField,
  type ExpenseFormValues,
} from './rules';
import { useLockedSubmission, type LockedControl } from './useLockedSubmission';

// "Nuevo gasto" (M7-12b D1), without any call. The pickers list active
// categories (the keyed label, sorted in the viewer's language) and active
// vendors only. The expense date defaults to the center's today, never
// later; the due date follows it until it is changed, never before it. On a
// 201 the page opens the new expense (the answer holds no amount, #115 E7).
// `…_unknown` / `…_inactive` reload the pickers and go on their field. The
// lost-answer rule is E2 (a): useLockedSubmission.

export type PickerState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly T[] };

export interface NewExpenseDialogProps {
  open: boolean;
  today: LocalDate;
  /** Every category (the dialog offers the active ones). */
  categories: PickerState<ExpenseCategory>;
  /** Every vendor (the dialog offers the active ones). */
  vendors: PickerState<Vendor>;
  /** Reloads both pickers. */
  onReloadPickers: () => Promise<void>;
  /** Creates the expense with the key; rejects with the error to show. */
  onSubmit: (body: ExpenseCreate, key: string) => Promise<void>;
  /** After the 201. */
  onSaved: () => void;
  onClose: (failed: AttemptFailure) => void;
  /** Stories: the values to start from. */
  initialValues?: Partial<ExpenseFormValues>;
}

export function NewExpenseDialog(props: NewExpenseDialogProps) {
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
  today,
  categories,
  vendors,
  onReloadPickers,
  onSubmit,
  onSaved,
  initialValues,
  busyRef,
  failedRef,
  onCancel,
}: NewExpenseDialogProps & LockedControl) {
  const { t, i18n } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const categoryLabel = useCategoryLabel();
  const id = useId();
  const submission = useLockedSubmission<ExpenseCreate>(busyRef, failedRef);
  const [values, setValues] = useState<ExpenseFormValues>(() => ({
    ...emptyExpenseForm(today),
    ...initialValues,
  }));
  const [dueTouched, setDueTouched] = useState(
    initialValues?.dueOn !== undefined,
  );
  const [errors, setErrors] = useState<ExpenseErrors>({});
  const [notice, setNotice] = useState('');
  const fieldId = (field: ExpenseField) => `${id}-${field}`;
  const locked = submission.locked;

  const collator = new Intl.Collator(i18n.resolvedLanguage);
  const activeCategories =
    categories.status === 'ready'
      ? categories.items
          .filter((category) => category.is_active)
          .map((category) => ({
            id: category.id,
            label: categoryLabel(category),
          }))
          .sort((a, b) => collator.compare(a.label, b.label))
      : [];
  const activeVendors =
    vendors.status === 'ready'
      ? vendors.items.filter((vendor) => vendor.is_active)
      : [];

  function change<K extends ExpenseField>(key: K, value: string) {
    setValues((current) => {
      const next = { ...current, [key]: value };
      if (key === 'incurredOn' && !dueTouched) next.dueOn = value;
      return next;
    });
    if (key === 'dueOn') setDueTouched(true);
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  function focusFirst(found: ExpenseErrors) {
    const field = EXPENSE_FIELD_ORDER.find((name) => found[name]);
    if (field) document.getElementById(fieldId(field))?.focus();
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice('');
    void submission.submit({
      build: () => {
        const found = validateExpenseForm(values, today);
        setErrors(found);
        if (Object.keys(found).length > 0) {
          focusFirst(found);
          return null;
        }
        return expenseCreateBody(values);
      },
      send: onSubmit,
      onSaved,
      onRefused: (caught) => {
        if (isCode(caught, ...RELOAD_PICKERS_AFTER)) {
          void onReloadPickers().then(() =>
            setNotice(t(`${D}.create.pickersReloaded`)),
          );
        }
        const found = expenseServerErrors(caught);
        if (!found) return false;
        setErrors(found);
        focusFirst(found);
        return true;
      },
    });
  }

  const pickerProblem =
    categories.status === 'error'
      ? categories
      : vendors.status === 'error'
        ? vendors
        : null;

  let content;
  if (pickerProblem) {
    content = (
      <ErrorAlert error={pickerProblem.error} onRetry={pickerProblem.onRetry} />
    );
  } else if (categories.status === 'loading' || vendors.status === 'loading') {
    content = <LoadingState rows={5} />;
  } else {
    const counter = (value: string, max: number) =>
      t(`${D}.counter`, { n: textLength(value), max });
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <FormField
            id={fieldId('categoryId')}
            label={t(`${D}.create.category`)}
            error={message(errors.categoryId)}
          >
            {(field) => (
              <select
                {...field}
                className={SELECT_CLASS}
                value={values.categoryId}
                disabled={locked}
                onChange={(event) => change('categoryId', event.target.value)}
              >
                <option value="">{t(`${D}.choose`)}</option>
                {activeCategories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.label}
                  </option>
                ))}
              </select>
            )}
          </FormField>
          <FormField
            id={fieldId('vendorId')}
            label={t(`${D}.create.vendor`)}
            error={message(errors.vendorId)}
          >
            {(field) => (
              <select
                {...field}
                className={SELECT_CLASS}
                value={values.vendorId}
                disabled={locked}
                onChange={(event) => change('vendorId', event.target.value)}
              >
                <option value="">{t(`${D}.create.noVendor`)}</option>
                {activeVendors.map((vendor) => (
                  <option key={vendor.id} value={vendor.id}>
                    {vendor.name}
                  </option>
                ))}
              </select>
            )}
          </FormField>
        </div>
        <FormField
          id={fieldId('description')}
          label={t(`${D}.create.description`)}
          counter={counter(values.description, DESCRIPTION_MAX)}
          error={message(errors.description)}
        >
          {(field) => (
            <Input
              {...field}
              type="text"
              autoComplete="off"
              className={INPUT_CLASS}
              value={values.description}
              disabled={locked}
              onChange={(event) => change('description', event.target.value)}
            />
          )}
        </FormField>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <FormField
            id={fieldId('amount')}
            label={t(`${D}.create.amount`)}
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
          <FormField
            id={fieldId('incurredOn')}
            label={t(`${D}.create.incurredOn`)}
            error={message(errors.incurredOn)}
          >
            {(field) => (
              <Input
                {...field}
                type="date"
                max={today}
                className={INPUT_CLASS}
                value={values.incurredOn}
                disabled={locked}
                onChange={(event) => change('incurredOn', event.target.value)}
              />
            )}
          </FormField>
          <FormField
            id={fieldId('dueOn')}
            label={t(`${D}.create.dueOn`)}
            hint={t(`${D}.create.dueOnHint`)}
            error={message(errors.dueOn)}
          >
            {(field) => (
              <Input
                {...field}
                type="date"
                min={values.incurredOn || undefined}
                className={INPUT_CLASS}
                value={values.dueOn}
                disabled={locked}
                onChange={(event) => change('dueOn', event.target.value)}
              />
            )}
          </FormField>
        </div>
        <FormField
          id={fieldId('receiptReference')}
          label={t(`${D}.create.receiptReference`)}
          counter={counter(values.receiptReference, RECEIPT_REFERENCE_MAX)}
          error={message(errors.receiptReference)}
        >
          {(field) => (
            <Input
              {...field}
              type="text"
              autoComplete="off"
              className={INPUT_CLASS}
              value={values.receiptReference}
              disabled={locked}
              onChange={(event) =>
                change('receiptReference', event.target.value)
              }
            />
          )}
        </FormField>
        <FormField
          id={fieldId('note')}
          label={t(`${D}.create.note`)}
          counter={counter(values.note, NOTE_MAX)}
          error={message(errors.note)}
        >
          {(field) => (
            <textarea
              {...field}
              rows={3}
              className={TEXTAREA_CLASS}
              value={values.note}
              disabled={locked}
              onChange={(event) => change('note', event.target.value)}
            />
          )}
        </FormField>
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
            className="min-h-11 md:min-h-9"
            disabled={submission.submitting}
          >
            {submission.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : submission.lost ? (
              t(`${D}.create.resend`)
            ) : (
              t(`${D}.create.submit`)
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-expense-dialog="create"
    >
      <DialogHeader>
        <DialogTitle>{t(`${D}.create.title`)}</DialogTitle>
        <DialogDescription>{t(`${D}.create.help`)}</DialogDescription>
      </DialogHeader>
      {submission.failure ? (
        <ErrorAlert
          error={submission.failure}
          message={submission.lost ? t(`${D}.create.lost`) : undefined}
        />
      ) : null}
      <p role="status" className="text-sm empty:hidden">
        {notice}
      </p>
      {content}
    </DialogContent>
  );
}
