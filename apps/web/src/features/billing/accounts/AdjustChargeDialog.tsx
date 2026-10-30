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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import type { ChargeItem, ManualAdjustmentCreate } from './api';
import { apiCents } from './cents';
import { CorrectionField } from './CorrectionField';
import {
  ADJUSTMENT_DIRECTIONS,
  ADJUSTMENT_STALE_CODES,
  adjustmentBody,
  adjustmentServerErrors,
  anyError,
  EMPTY_ADJUSTMENT,
  isCode,
  REASON_MAX,
  validateAdjustment,
  type AdjustmentErrors,
  type AdjustmentValues,
} from './corrections';
import { useAccountLabels } from './labels';
import { useCorrectionSubmission } from './useCorrectionSubmission';

// "Adjust" a posted charge (#386 A3; #100 C2, R3), without any call: a
// credit (reduce) or a debit (increase), the amount and the reason. The
// current net is shown, and a credit above it is blocked here (the server
// checks again: 409 charge_net_negative). The route needs a recent
// password confirmation: apiCall opens the password dialog on
// `reauth_required` and retries with the SAME key (useCorrectionSubmission
// computes it before the call). A 409 charge_net_negative or
// package_purchase_not_adjustable is an alert and reloads the charge.

export interface AdjustChargeDialogProps {
  open: boolean;
  charge: Pick<
    ChargeItem,
    'id' | 'net' | 'description' | 'kind' | 'student' | 'service_date'
  > | null;
  onSubmit: (
    body: ManualAdjustmentCreate,
    idempotencyKey: string,
  ) => Promise<void>;
  /** Reloads the charge (and the account) after a 409. */
  onStale?: () => void;
  onClose: () => void;
  initialValues?: Partial<AdjustmentValues>;
  initialErrors?: AdjustmentErrors;
}

export function AdjustChargeDialog(props: AdjustChargeDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open && props.charge ? (
        <Body {...props} charge={props.charge} busy={busy} />
      ) : null}
    </Dialog>
  );
}

function Body({
  charge,
  onSubmit,
  onStale,
  onClose,
  initialValues,
  initialErrors = {},
  busy,
}: AdjustChargeDialogProps & {
  charge: NonNullable<AdjustChargeDialogProps['charge']>;
  busy: { current: boolean };
}) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const submission = useCorrectionSubmission(busy);
  const [values, setValues] = useState<AdjustmentValues>(() => ({
    ...EMPTY_ADJUSTMENT,
    ...initialValues,
  }));
  const [errors, setErrors] = useState<AdjustmentErrors>(initialErrors);
  const net = apiCents(charge.net);

  function change<K extends keyof AdjustmentValues>(
    key: K,
    value: AdjustmentValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({
      ...current,
      [key === 'direction' ? 'amount' : key]: undefined,
    }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateAdjustment(values, net);
    setErrors(found);
    if (anyError(found)) {
      document
        .getElementById(found.amount ? `${id}-amount` : `${id}-reason`)
        ?.focus();
      return;
    }
    await submission.run(adjustmentBody(values), onSubmit, {
      onFields: (caught) => {
        const fromServer = adjustmentServerErrors(caught);
        if (!fromServer) return false;
        setErrors(fromServer);
        return true;
      },
      onStale: (caught) => {
        if (isCode(caught, ...ADJUSTMENT_STALE_CODES)) onStale?.();
      },
    });
  }

  const { submitting } = submission;
  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.accounts.adjust.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.accounts.adjust.help')}
        </DialogDescription>
      </DialogHeader>
      <p className="text-sm">
        <span className="font-medium">{labels.chargeLabel(charge)}</span>
        {' · '}
        {charge.student.name}
        {' · '}
        {labels.date(charge.service_date)}
      </p>
      <p className="text-sm font-medium" data-current-net={charge.net}>
        {t('billing.accounts.adjust.currentNet', {
          amount: labels.money(charge.net),
        })}
      </p>
      {submission.failure ? <ErrorAlert error={submission.failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.accounts.adjust.direction')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {ADJUSTMENT_DIRECTIONS.map((direction) => (
              <label
                key={direction}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name={`${id}-direction`}
                  value={direction}
                  className="size-4 accent-primary"
                  checked={values.direction === direction}
                  disabled={submitting}
                  onChange={() => change('direction', direction)}
                />
                {t(`billing.accounts.adjust.directions.${direction}`)}
              </label>
            ))}
          </div>
        </fieldset>
        <CorrectionField
          id={`${id}-amount`}
          label={t('billing.accounts.adjust.fields.amount')}
          help={t('billing.accounts.adjust.hints.amount')}
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
        <CorrectionField
          id={`${id}-reason`}
          label={t('billing.accounts.adjust.fields.reason')}
          help={t('billing.accounts.adjust.hints.reason')}
          error={errors.reason}
        >
          {(wiring) => (
            <textarea
              {...wiring}
              rows={2}
              className={TEXTAREA_CLASS}
              maxLength={REASON_MAX}
              value={values.reason}
              onChange={(event) => change('reason', event.target.value)}
            />
          )}
        </CorrectionField>
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
              <Spinner label={t('billing.accounts.adjust.saving')} showLabel />
            ) : (
              t('billing.accounts.adjust.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
