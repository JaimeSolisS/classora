import { useId, useRef, useState } from 'react';
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
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import type { ExpenseCategory, ExpenseTemplate, Vendor } from '../api';
import { FormField } from '../expenses/FormField';
import type { PickerState } from '../expenses/NewExpenseDialog';
import { useCategoryLabel } from '../labels';
import { RowNoticeAlert } from '../RowNoticeAlert';
import { useRowSave, type RowNotice, type SaveResult } from '../useRowSave';
import {
  earliestEnd,
  emptyTemplateForm,
  FREQUENCIES,
  RELOAD_PICKERS_AFTER,
  startsOnWindow,
  TEMPLATE_FIELD_ORDER,
  TEMPLATE_NAME_MAX,
  templateForm,
  templateServerErrors,
  validateTemplateForm,
  type TemplateErrors,
  type TemplateField,
  type TemplateFormValues,
} from './rules';

// "Nueva plantilla" and "Editar" (M7-13b D1, D2, D5, E2, E3), without any
// API call. The pickers list active rows with the keyed label; an edit also
// keeps the template's current category or vendor when it was deactivated
// since, so the select shows it (it is only sent when changed). The create
// sets the frequency and the first period; an edit shows them read-only with
// the E3 hint and notice. The #458 config-page rules come from useRowSave:
// a field refusal goes on its field, `version_conflict` reloads inside the
// dialog, a `404` locks it, and a create retried after a lost answer that
// ends in `expense_template_exists` reloads the list and says the template
// may already have been saved.

export interface TemplateDialogProps {
  open: boolean;
  /** The template being edited, as the page has it now; null creates one. */
  template: ExpenseTemplate | null;
  today: LocalDate;
  /** Every category and vendor (the dialog offers the active ones). */
  categories: PickerState<ExpenseCategory>;
  vendors: PickerState<Vendor>;
  /** Stores `values` based on `base`; rejects with the refusal. A refusal
   * about the category or vendor has already reloaded the pickers. */
  onSubmit: (
    values: TemplateFormValues,
    base: ExpenseTemplate | null,
  ) => Promise<SaveResult>;
  /** Refetches the list or the detail; resolves whether it reloaded. */
  onReload: () => Promise<boolean>;
  onClose: (saved: boolean) => void;
  /** Stories: the values, errors and notice to start with. */
  initialValues?: Partial<TemplateFormValues>;
  initialErrors?: TemplateErrors;
  initialNotice?: RowNotice;
}

export function TemplateDialog(props: TemplateDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose(false);
      }}
    >
      {props.open ? <Body {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const D = 'finance.templates.dialog';

function Body({
  template,
  today,
  categories,
  vendors,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors,
  initialNotice,
  busyRef,
}: TemplateDialogProps & { busyRef: { current: boolean } }) {
  const { t, i18n } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const categoryLabel = useCategoryLabel();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const fieldId = (field: TemplateField) => `${id}-${field}`;
  const [pickerNotice, setPickerNotice] = useState('');
  const form = useRowSave<ExpenseTemplate, TemplateFormValues, TemplateField>({
    row: template,
    initial: (row) => (row ? templateForm(row) : emptyTemplateForm(today)),
    initialValues: initialValues
      ? {
          ...(template ? templateForm(template) : emptyTemplateForm(today)),
          ...initialValues,
        }
      : undefined,
    initialErrors,
    initialNotice,
    validate: (values) => validateTemplateForm(values, today, template),
    serverErrors: (caught) => {
      setPickerNotice(
        isCode(caught, ...RELOAD_PICKERS_AFTER)
          ? t(`${D}.pickersReloaded`)
          : '',
      );
      return templateServerErrors(caught);
    },
    onSubmit,
    onReload,
    onClose,
    focusFirst: (found) => {
      const field = TEMPLATE_FIELD_ORDER.find((name) => found[name]);
      // After the render that enables the fields again (a refusal arrives
      // while they are disabled).
      if (field) {
        window.setTimeout(() =>
          document.getElementById(fieldId(field))?.focus(),
        );
      }
    },
    busyRef,
  });
  const { base, values, errors } = form;
  const editing = base !== null;
  const locked = form.locked;

  function change<K extends TemplateField>(
    key: K,
    value: TemplateFormValues[K],
  ) {
    form.setValues((current) => ({ ...current, [key]: value }));
  }

  const collator = new Intl.Collator(i18n.resolvedLanguage);
  const categoryOptions =
    categories.status === 'ready'
      ? categories.items
          .filter(
            (category) =>
              category.is_active || category.id === base?.category.id,
          )
          .map((category) => ({
            id: category.id,
            label: category.is_active
              ? categoryLabel(category)
              : t(`${D}.inactiveOption`, { name: categoryLabel(category) }),
          }))
          .sort((a, b) => collator.compare(a.label, b.label))
      : [];
  const vendorOptions =
    vendors.status === 'ready'
      ? vendors.items
          .filter(
            (vendor) => vendor.is_active || vendor.id === base?.vendor?.id,
          )
          .map((vendor) => ({
            id: vendor.id,
            label: vendor.is_active
              ? vendor.name
              : t(`${D}.inactiveOption`, { name: vendor.name }),
          }))
      : [];
  const startWindow = startsOnWindow(today);
  const endMin = base ? earliestEnd(base) : values.startsOn || startWindow.min;
  const pastStart =
    !editing && values.startsOn !== '' && values.startsOn < today;

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
    content = (
      <form
        method="post"
        noValidate
        onSubmit={form.onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <FormField
          id={fieldId('name')}
          label={t(`${D}.name`)}
          counter={t(`${D}.counter`, {
            n: Array.from(values.name.trim()).length,
            max: TEMPLATE_NAME_MAX,
          })}
          error={message(errors.name)}
        >
          {(field) => (
            <Input
              {...field}
              type="text"
              autoComplete="off"
              className={INPUT_CLASS}
              value={values.name}
              disabled={locked}
              onChange={(event) => change('name', event.target.value)}
            />
          )}
        </FormField>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <FormField
            id={fieldId('categoryId')}
            label={t(`${D}.category`)}
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
                {categoryOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
          </FormField>
          <FormField
            id={fieldId('vendorId')}
            label={t(`${D}.vendor`)}
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
                <option value="">{t(`${D}.noVendor`)}</option>
                {vendorOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
          </FormField>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <FormField
            id={fieldId('amount')}
            label={t(`${D}.amount`)}
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
            id={fieldId('dueOffset')}
            label={t(`${D}.dueOffset`)}
            hint={t(`${D}.dueOffsetHint`)}
            error={message(errors.dueOffset)}
          >
            {(field) => (
              <Input
                {...field}
                type="text"
                inputMode="numeric"
                autoComplete="off"
                className={INPUT_CLASS}
                value={values.dueOffset}
                disabled={locked}
                onChange={(event) => change('dueOffset', event.target.value)}
              />
            )}
          </FormField>
        </div>
        {editing ? (
          <div className="flex min-w-0 flex-col gap-2" data-template-fixed>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="flex min-w-0 flex-col gap-1">
                <dt className="text-sm font-medium">{t(`${D}.frequency`)}</dt>
                <dd>{t(`finance.templates.frequencies.${base.frequency}`)}</dd>
              </div>
              <div className="flex min-w-0 flex-col gap-1">
                <dt className="text-sm font-medium">{t(`${D}.startsOn`)}</dt>
                <dd>{formatLocalDate(base.starts_on)}</dd>
              </div>
            </dl>
            <p className="text-sm text-muted-foreground">
              {t(`${D}.fixedHint`)}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FormField
              id={fieldId('frequency')}
              label={t(`${D}.frequency`)}
              error={message(errors.frequency)}
            >
              {(field) => (
                <select
                  {...field}
                  className={SELECT_CLASS}
                  value={values.frequency}
                  disabled={locked}
                  onChange={(event) =>
                    change(
                      'frequency',
                      event.target.value as TemplateFormValues['frequency'],
                    )
                  }
                >
                  {FREQUENCIES.map((frequency) => (
                    <option key={frequency} value={frequency}>
                      {t(`finance.templates.frequencies.${frequency}`)}
                    </option>
                  ))}
                </select>
              )}
            </FormField>
            <FormField
              id={fieldId('startsOn')}
              label={t(`${D}.startsOn`)}
              hint={t(`${D}.startsOnHint`)}
              error={message(errors.startsOn)}
            >
              {(field) => (
                <Input
                  {...field}
                  type="date"
                  min={startWindow.min}
                  max={startWindow.max}
                  className={INPUT_CLASS}
                  value={values.startsOn}
                  disabled={locked}
                  onChange={(event) => change('startsOn', event.target.value)}
                />
              )}
            </FormField>
          </div>
        )}
        {pastStart ? (
          <p
            role="status"
            className="rounded-md border border-border bg-muted p-3 text-sm"
            data-past-start
          >
            {t(`${D}.pastStart`)}
          </p>
        ) : null}
        <FormField
          id={fieldId('endsOn')}
          label={t(`${D}.endsOn`)}
          hint={
            editing && base.last_period_start
              ? `${t(`${D}.endsOnHint`)} ${t(`${D}.endMin`, {
                  date: formatLocalDate(earliestEnd(base)),
                })}`
              : t(`${D}.endsOnHint`)
          }
          error={message(errors.endsOn)}
        >
          {(field) => (
            <Input
              {...field}
              type="date"
              min={endMin}
              className={INPUT_CLASS}
              value={values.endsOn}
              disabled={locked}
              onChange={(event) => change('endsOn', event.target.value)}
            />
          )}
        </FormField>
        {editing ? (
          <p
            className="rounded-md border border-border p-3 text-sm"
            data-edit-notice
          >
            {t(`${D}.editNotice`)}
          </p>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={form.submitting}
            onClick={() => onClose(false)}
          >
            {t('finance.cancel')}
          </Button>
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={locked}
          >
            {form.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : editing ? (
              t('finance.save')
            ) : (
              t(`${D}.submitCreate`)
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
      data-template-dialog={editing ? 'edit' : 'create'}
    >
      <DialogHeader>
        <DialogTitle>
          {editing ? t(`${D}.editTitle`) : t(`${D}.createTitle`)}
        </DialogTitle>
        <DialogDescription>
          {editing ? t(`${D}.editHelp`) : t(`${D}.createHelp`)}
        </DialogDescription>
      </DialogHeader>
      <RowNoticeAlert notice={form.notice} kind="templates" />
      {form.failure ? (
        <ErrorAlert
          error={form.failure}
          onRetry={
            form.conflict && !form.reloading
              ? () => void form.reload()
              : undefined
          }
          retryLabel={form.conflict ? t('finance.reload') : undefined}
        />
      ) : null}
      <p role="status" className="text-sm empty:hidden">
        {pickerNotice}
      </p>
      {content}
    </DialogContent>
  );
}
