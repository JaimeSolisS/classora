import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { subjectsByLevel } from '@/features/scheduling/create/rules';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import {
  MINUTES_MAX,
  MINUTES_MIN,
  SESSIONS_MAX,
  SESSIONS_MIN,
  VALIDITY_MAX,
  VALIDITY_MIN,
  type PackageUnit,
} from '@/lib/packages';
import type { LocalDate } from '@/lib/time';
import type { SubjectOption } from '../pricing/api';
import {
  emptyProductForm,
  PRODUCT_FIELD_ORDER,
  productServerErrors,
  validateProductForm,
  type ProductErrors,
  type ProductField,
  type ProductFormValues,
} from './rules';

// "New package" (#377 B2), without any API call: the name, the unit
// (Sessions or Hours; an hours quantity is typed in hours with 0.25 steps
// and sent as minutes), the validity in days, the subject (optional: "Any
// subject"), the price (the #89b money input) and the date it takes
// effect (center today by default). `onSubmit` resolves once the product
// is stored (the page then opens it); a 422 puts each field error on its
// field, anything else is an alert. Nothing typed is lost on any error.

export interface ProductDialogProps {
  open: boolean;
  today: LocalDate;
  /** The active subjects; null while they load. */
  subjects: readonly SubjectOption[] | null;
  subjectsError?: unknown;
  onRetrySubjects?: () => void;
  /** Stores the product; rejects with the error to show. */
  onSubmit: (values: ProductFormValues) => Promise<void>;
  onClose: () => void;
  /** Stories: the values and errors to start from. */
  initialValues?: ProductFormValues;
  initialErrors?: ProductErrors;
}

export function ProductDialog(props: ProductDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const UNITS: readonly PackageUnit[] = ['sessions', 'minutes'];

function DialogBody({
  today,
  subjects,
  subjectsError,
  onRetrySubjects,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: ProductDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<ProductFormValues>(
    () => initialValues ?? emptyProductForm(today),
  );
  const [errors, setErrors] = useState<ProductErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: ProductField) => `${id}-${field}`;
  const errorId = (field: ProductField) => `${id}-${field}-error`;
  const helpId = (field: ProductField) => `${id}-${field}-help`;

  function change<K extends ProductField>(
    field: K,
    value: ProductFormValues[K],
  ) {
    setValues((current) => ({
      ...current,
      [field]: value,
      // A new unit changes what the quantity means.
      ...(field === 'unit' && value !== current.unit ? { quantity: '' } : {}),
    }));
  }

  function focusFirst(found: ProductErrors) {
    const first = PRODUCT_FIELD_ORDER.find((field) => found[field]);
    if (!first) return;
    const target = document.getElementById(fieldId(first));
    const focusable =
      target instanceof HTMLInputElement || target instanceof HTMLSelectElement
        ? target
        : target?.querySelector<HTMLElement>('input, select');
    focusable?.focus();
  }

  async function submit() {
    setFailure(null);
    const found = validateProductForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (caught) {
      const fromServer = productServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void submit();
    }
  }

  const describedBy = (field: ProductField, help = false) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const invalid = (field: ProductField) => (errors[field] ? true : undefined);

  function field(
    name: ProductField,
    label: string,
    control: ReactNode,
    help?: ReactNode,
  ) {
    return (
      <div className="flex min-w-0 flex-col gap-2">
        <label htmlFor={fieldId(name)} className="text-sm font-medium">
          {label}
        </label>
        {control}
        {help ? (
          <p id={helpId(name)} className="text-sm text-muted-foreground">
            {help}
          </p>
        ) : null}
        <FieldError id={errorId(name)} message={message(errors[name])} />
      </div>
    );
  }

  let content;
  if (subjectsError) {
    content = <ErrorAlert error={subjectsError} onRetry={onRetrySubjects} />;
  } else if (!subjects) {
    content = <LoadingState rows={6} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        {field(
          'name',
          t('billing.packages.fields.name'),
          <Input
            id={fieldId('name')}
            type="text"
            autoComplete="off"
            maxLength={200}
            value={values.name}
            aria-invalid={invalid('name')}
            aria-describedby={describedBy('name')}
            onChange={(event) => change('name', event.target.value)}
          />,
        )}
        <fieldset id={fieldId('unit')} className="flex flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.packages.fields.unit')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {UNITS.map((unit) => (
              <label
                key={unit}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name={`${id}-unit`}
                  value={unit}
                  checked={values.unit === unit}
                  className="size-4 accent-primary"
                  onChange={() => change('unit', unit)}
                />
                {t(`billing.packages.unitNames.${unit}`)}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'quantity',
            values.unit === 'sessions'
              ? t('billing.packages.fields.quantitySessions')
              : t('billing.packages.fields.quantityHours'),
            <Input
              id={fieldId('quantity')}
              type="text"
              inputMode={values.unit === 'sessions' ? 'numeric' : 'decimal'}
              autoComplete="off"
              value={values.quantity}
              aria-invalid={invalid('quantity')}
              aria-describedby={describedBy('quantity', true)}
              onChange={(event) => change('quantity', event.target.value)}
            />,
            values.unit === 'sessions'
              ? t('billing.packages.hints.sessions', {
                  min: SESSIONS_MIN,
                  max: SESSIONS_MAX,
                })
              : t('billing.packages.hints.hours', {
                  min: MINUTES_MIN / 60,
                  max: MINUTES_MAX / 60,
                }),
          )}
          {field(
            'validity',
            t('billing.packages.fields.validity'),
            <Input
              id={fieldId('validity')}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={values.validity}
              aria-invalid={invalid('validity')}
              aria-describedby={describedBy('validity', true)}
              onChange={(event) => change('validity', event.target.value)}
            />,
            t('billing.packages.hints.validity', {
              min: VALIDITY_MIN,
              max: VALIDITY_MAX,
            }),
          )}
        </div>
        {field(
          'subjectId',
          t('billing.packages.fields.subject'),
          <select
            id={fieldId('subjectId')}
            className={SELECT_CLASS}
            value={values.subjectId}
            aria-invalid={invalid('subjectId')}
            aria-describedby={describedBy('subjectId', true)}
            onChange={(event) => change('subjectId', event.target.value)}
          >
            <option value="">{t('billing.packages.anySubject')}</option>
            {subjectsByLevel(subjects).map(([levelName, group]) => (
              <optgroup key={levelName} label={levelName}>
                {group.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {subject.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>,
          t('billing.packages.hints.subject'),
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'amount',
            t('billing.packages.fields.amount'),
            <Input
              id={fieldId('amount')}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={values.amount}
              aria-invalid={invalid('amount')}
              aria-describedby={describedBy('amount', true)}
              onChange={(event) => change('amount', event.target.value)}
            />,
            t('billing.prices.hints.amount'),
          )}
          {field(
            'effectiveFrom',
            t('billing.packages.fields.effectiveFrom'),
            <Input
              id={fieldId('effectiveFrom')}
              type="date"
              value={values.effectiveFrom}
              aria-invalid={invalid('effectiveFrom')}
              aria-describedby={describedBy('effectiveFrom', true)}
              onChange={(event) => change('effectiveFrom', event.target.value)}
            />,
            t('billing.packages.hints.effectiveFrom'),
          )}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.prices.saving')} showLabel />
            ) : (
              t('billing.packages.products.create')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.packages.products.add')}</DialogTitle>
        <DialogDescription>
          {t('billing.packages.products.fixedHint')}
        </DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      {content}
    </DialogContent>
  );
}
