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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import type { LocalDate } from '@/lib/time';
import type { ManualChargeCreate } from './api';
import { CorrectionField } from './CorrectionField';
import {
  anyError,
  DATE_MIN,
  DESCRIPTION_MAX,
  emptyManualCharge,
  MANUAL_CHARGE_FIELD_ORDER,
  manualChargeBody,
  manualChargeMaxDate,
  manualChargeServerErrors,
  validateManualCharge,
  type ManualChargeErrors,
  type ManualChargeField,
  type ManualChargeValues,
} from './corrections';
import { useCorrectionSubmission } from './useCorrectionSubmission';

// "Add charge" (#386 A1; #100 C1), without any call: the student (the
// account's students), a description, the amount, the service date (center
// today by default) and the due date (the service date by default, kept as
// entered: no family offset). No password confirmation. The idempotent
// submission, the pending state and the errors follow
// useCorrectionSubmission (D1-D3).

export interface ManualChargeDialogProps {
  open: boolean;
  today: LocalDate;
  students: readonly { id: string; name: string }[];
  onSubmit: (body: ManualChargeCreate, idempotencyKey: string) => Promise<void>;
  onClose: () => void;
  /** Stories: the values to start from. */
  initialValues?: Partial<ManualChargeValues>;
  /** Stories: an error shown as if the last submission had failed. */
  initialErrors?: ManualChargeErrors;
}

export function ManualChargeDialog(props: ManualChargeDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open ? <Body {...props} busy={busy} /> : null}
    </Dialog>
  );
}

function Body({
  today,
  students,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  busy,
}: ManualChargeDialogProps & { busy: { current: boolean } }) {
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  const id = useId();
  const submission = useCorrectionSubmission(busy);
  const [values, setValues] = useState<ManualChargeValues>(() => ({
    ...emptyManualCharge(today, students.length === 1 ? students[0]!.id : ''),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<ManualChargeErrors>(initialErrors);
  const fieldId = (field: ManualChargeField) => `${id}-${field}`;
  const max = manualChargeMaxDate(today);

  function change(field: ManualChargeField, value: string) {
    setValues((current) => {
      const next = { ...current, [field]: value };
      // The due date follows the service date until it is changed (choice).
      if (field === 'serviceDate' && current.dueDate === current.serviceDate) {
        next.dueDate = value;
      }
      return next;
    });
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function focusFirst(found: ManualChargeErrors) {
    const field = MANUAL_CHARGE_FIELD_ORDER.find((name) => found[name]);
    if (field) document.getElementById(fieldId(field))?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateManualCharge(values, today);
    setErrors(found);
    if (anyError(found)) {
      focusFirst(found);
      return;
    }
    await submission.run(manualChargeBody(values), onSubmit, {
      onFields: (caught) => {
        const fromServer = manualChargeServerErrors(caught);
        if (!fromServer) return false;
        setErrors(fromServer);
        focusFirst(fromServer);
        return true;
      },
    });
  }

  const { submitting } = submission;
  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.accounts.manualCharge.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.accounts.manualCharge.help')}
        </DialogDescription>
      </DialogHeader>
      {submission.failure ? <ErrorAlert error={submission.failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <CorrectionField
            id={fieldId('studentId')}
            label={t('billing.accounts.manualCharge.fields.student')}
            error={errors.studentId}
          >
            {(wiring) => (
              <select
                {...wiring}
                className={SELECT_CLASS}
                value={values.studentId}
                onChange={(event) => change('studentId', event.target.value)}
              >
                <option value="">{t('billing.prices.choose')}</option>
                {students.map((student) => (
                  <option key={student.id} value={student.id}>
                    {student.name}
                  </option>
                ))}
              </select>
            )}
          </CorrectionField>
          <CorrectionField
            id={fieldId('amount')}
            label={t('billing.accounts.manualCharge.fields.amount')}
            help={t('billing.prices.hints.amount')}
            error={errors.amount}
          >
            {(wiring) => (
              <Input
                {...wiring}
                type="text"
                inputMode="decimal"
                autoComplete="off"
                value={values.amount}
                onChange={(event) => change('amount', event.target.value)}
              />
            )}
          </CorrectionField>
        </div>
        <CorrectionField
          id={fieldId('description')}
          label={t('billing.accounts.manualCharge.fields.description')}
          help={t('billing.accounts.manualCharge.hints.description')}
          error={errors.description}
        >
          {(wiring) => (
            <Input
              {...wiring}
              type="text"
              autoComplete="off"
              maxLength={DESCRIPTION_MAX}
              value={values.description}
              onChange={(event) => change('description', event.target.value)}
            />
          )}
        </CorrectionField>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <CorrectionField
            id={fieldId('serviceDate')}
            label={t('billing.accounts.manualCharge.fields.serviceDate')}
            error={errors.serviceDate}
          >
            {(wiring) => (
              <Input
                {...wiring}
                type="date"
                min={DATE_MIN}
                max={max}
                value={values.serviceDate}
                onChange={(event) => change('serviceDate', event.target.value)}
              />
            )}
          </CorrectionField>
          <CorrectionField
            id={fieldId('dueDate')}
            label={t('billing.accounts.manualCharge.fields.dueDate')}
            help={t('billing.accounts.manualCharge.hints.dueDate')}
            error={errors.dueDate}
          >
            {(wiring) => (
              <Input
                {...wiring}
                type="date"
                min={DATE_MIN}
                max={max}
                value={values.dueDate}
                onChange={(event) => change('dueDate', event.target.value)}
              />
            )}
          </CorrectionField>
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
              <Spinner
                label={t('billing.accounts.manualCharge.saving')}
                showLabel
              />
            ) : (
              t('billing.accounts.manualCharge.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
