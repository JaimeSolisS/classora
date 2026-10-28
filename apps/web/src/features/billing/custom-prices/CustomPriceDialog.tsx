import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
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
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import type { LocalDate } from '@/lib/time';
import type { ServicePrice } from '../pricing/api';
import { TargetPicker, type TargetPickerProps } from '../TargetPicker';
import type { TargetKind, TargetOption } from '../targets';
import { useCustomPriceLabels } from './labels';
import {
  CUSTOM_PRICE_FIELD_ORDER,
  customPriceServerErrors,
  emptyCustomPriceForm,
  validateCustomPriceForm,
  type CustomPriceErrors,
  type CustomPriceField,
  type CustomPriceFormValues,
} from './rules';

// "Add custom price" (#367 E4), without any API call: the student or the
// family (preselected from the page's filter or deep link), the catalog
// price (per session and full course items, with their price today), the
// amount and the dates (from today; the end optional). A 409
// custom_price_overlap shows inside the dialog; a 422 puts each field error
// on its field (custom_price_effective_from_invalid on the start date).
// Nothing typed is lost on any error.

export interface CustomPriceDialogProps {
  open: boolean;
  today: LocalDate;
  /** The student or family to start with (the page's filter). */
  initialTarget?: TargetOption | null;
  /** The catalog prices; null while they load. */
  catalog: readonly ServicePrice[] | null;
  catalogError?: unknown;
  onRetryCatalog?: () => void;
  /** Stores the custom price; rejects with the error to show. */
  onSubmit: (values: CustomPriceFormValues) => Promise<void>;
  onClose: () => void;
  /** Stories: the values, errors and search to start from. */
  initialValues?: CustomPriceFormValues;
  initialErrors?: CustomPriceErrors;
  search?: TargetPickerProps['search'];
}

export function CustomPriceDialog(props: CustomPriceDialogProps) {
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

function DialogBody({
  today,
  initialTarget = null,
  catalog,
  catalogError,
  onRetryCatalog,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  search,
  busyRef,
}: CustomPriceDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const labels = useCustomPriceLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<CustomPriceFormValues>(
    () => initialValues ?? emptyCustomPriceForm(today, initialTarget),
  );
  const [kind, setKind] = useState<TargetKind>(
    () => values.target?.kind ?? 'student',
  );
  const [errors, setErrors] = useState<CustomPriceErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: CustomPriceField) => `${id}-${field}`;
  const errorId = (field: CustomPriceField) => `${id}-${field}-error`;
  const helpId = (field: CustomPriceField) => `${id}-${field}-help`;

  function change<K extends CustomPriceField>(
    field: K,
    value: CustomPriceFormValues[K],
  ) {
    setValues((current) => ({ ...current, [field]: value }));
    setFailure(null);
  }

  function focusFirst(found: CustomPriceErrors) {
    const first = CUSTOM_PRICE_FIELD_ORDER.find((field) => found[field]);
    if (!first) return;
    const element = document.getElementById(fieldId(first));
    if (
      element instanceof HTMLInputElement ||
      element instanceof HTMLSelectElement
    ) {
      element.focus();
    } else {
      element?.querySelector<HTMLInputElement>('input[type="search"]')?.focus();
    }
  }

  async function submit() {
    setFailure(null);
    const found = validateCustomPriceForm(values, today);
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
      const fromServer = customPriceServerErrors(caught);
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
    if (busyRef.current) {
      return;
    }
    void submit();
  }

  const describedBy = (field: CustomPriceField, help = false) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const invalid = (field: CustomPriceField) =>
    errors[field] ? true : undefined;

  function field(
    name: CustomPriceField,
    label: string,
    control: ReactNode,
    help?: string,
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

  const overlap =
    failure instanceof ApiError && failure.code === 'custom_price_overlap';

  let content;
  if (catalogError) {
    content = <ErrorAlert error={catalogError} onRetry={onRetryCatalog} />;
  } else if (!catalog) {
    content = <LoadingState rows={5} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div id={fieldId('target')}>
          <TargetPicker
            legend={t('billing.customPrices.fields.targetKind')}
            kind={kind}
            onKind={(next) => {
              setKind(next);
              change('target', null);
            }}
            selected={values.target}
            onSelect={(target) => {
              change('target', target);
              setErrors((current) => ({ ...current, target: undefined }));
            }}
            errorMessage={message(errors.target)}
            search={search}
          />
        </div>
        {field(
          'servicePriceId',
          t('billing.customPrices.fields.service'),
          <select
            id={fieldId('servicePriceId')}
            className={SELECT_CLASS}
            value={values.servicePriceId}
            aria-invalid={invalid('servicePriceId')}
            aria-describedby={describedBy('servicePriceId', true)}
            onChange={(event) => change('servicePriceId', event.target.value)}
          >
            <option value="">{t('billing.prices.choose')}</option>
            {catalog.map((item) => (
              <option key={item.id} value={item.id}>
                {labels.option(item)}
              </option>
            ))}
          </select>,
          catalog.length === 0
            ? t('billing.customPrices.hints.noCatalog')
            : t('billing.customPrices.hints.service'),
        )}
        {field(
          'amount',
          t('billing.customPrices.fields.amount'),
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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field(
            'effectiveFrom',
            t('billing.customPrices.fields.effectiveFrom'),
            <Input
              id={fieldId('effectiveFrom')}
              type="date"
              min={today}
              value={values.effectiveFrom}
              aria-invalid={invalid('effectiveFrom')}
              aria-describedby={describedBy('effectiveFrom', true)}
              onChange={(event) => change('effectiveFrom', event.target.value)}
            />,
            t('billing.customPrices.hints.effectiveFrom'),
          )}
          {field(
            'expiresOn',
            t('billing.customPrices.fields.expiresOn'),
            <Input
              id={fieldId('expiresOn')}
              type="date"
              min={values.effectiveFrom || today}
              value={values.expiresOn}
              aria-invalid={invalid('expiresOn')}
              aria-describedby={describedBy('expiresOn', true)}
              onChange={(event) => change('expiresOn', event.target.value)}
            />,
            t('billing.customPrices.hints.expiresOn'),
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
              t('billing.prices.save')
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
        <DialogTitle>{t('billing.customPrices.add')}</DialogTitle>
        <DialogDescription>
          {t('billing.customPrices.addNote')}
        </DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          message={
            overlap
              ? values.target?.kind === 'family'
                ? t('billing.customPrices.overlap.family')
                : t('billing.customPrices.overlap.student')
              : undefined
          }
        />
      ) : null}
      {content}
    </DialogContent>
  );
}
