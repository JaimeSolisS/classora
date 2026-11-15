import { useId, useRef, useState, type FormEvent } from 'react';
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
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { CorrectionField } from '../accounts/CorrectionField';
import { anyError, isCode } from '../accounts/corrections';
import { useCorrectionSubmission } from '../accounts/useCorrectionSubmission';
import type { PackageCancellationCreate, SoldPackage } from './api';
import { usePackageLabels } from './labels';
import {
  cancelBody,
  cancelServerErrors,
  CANCEL_REASON_MAX,
  EMPTY_CANCEL_FORM,
  moneyCents,
  proposedCreditCents,
  validateCancelForm,
  type CancelErrors,
  type CancelFormValues,
} from './rules';

// "Cancel" a sold package (#386 C1; #100 C5, R6), without any call: the
// units left, the purchase charge's net and the proposed credit (net ×
// units left ÷ units granted, labelled as an estimate: the server
// decides), an optional credit from 0.00 to the net, the reason, and the
// warning that sessions the package covered but hadn't used are charged
// normally. Needs a recent password confirmation (apiCall's dialog, the
// same key on the retry). A 409 package_not_cancellable or
// charge_net_negative is an alert and reloads the list.

export type PurchaseChargeState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  /** `net` null: the package has no purchase charge. */
  | { status: 'ready'; net: string | null };

export interface CancelPackageDialogProps {
  open: boolean;
  pkg: SoldPackage | null;
  charge: PurchaseChargeState;
  onSubmit: (
    body: PackageCancellationCreate,
    idempotencyKey: string,
  ) => Promise<void>;
  onStale?: () => void;
  onClose: () => void;
  initialValues?: Partial<CancelFormValues>;
  initialErrors?: CancelErrors;
}

/** The 409s after which the list is read again. */
const STALE_CODES = ['package_not_cancellable', 'charge_net_negative'];

function centsText(cents: number): string {
  return `${Math.trunc(cents / 100)}.${String(cents % 100).padStart(2, '0')}`;
}

export function CancelPackageDialog(props: CancelPackageDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open && props.pkg ? (
        <Body {...props} pkg={props.pkg} busy={busy} />
      ) : null}
    </Dialog>
  );
}

function Body({
  pkg,
  charge,
  onSubmit,
  onStale,
  onClose,
  initialValues,
  initialErrors = {},
  busy,
}: CancelPackageDialogProps & {
  pkg: SoldPackage;
  busy: { current: boolean };
}) {
  const { t } = useTranslation();
  const labels = usePackageLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const submission = useCorrectionSubmission(busy);
  const [values, setValues] = useState<CancelFormValues>(() => ({
    ...EMPTY_CANCEL_FORM,
    ...initialValues,
  }));
  const [errors, setErrors] = useState<CancelErrors>(initialErrors);
  const net =
    charge.status === 'ready' && charge.net !== null
      ? moneyCents(charge.net)
      : null;

  function change(field: keyof CancelFormValues, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (charge.status !== 'ready') return;
    const found = validateCancelForm(values, net);
    setErrors(found);
    if (anyError(found)) {
      document
        .getElementById(found.credit ? `${id}-credit` : `${id}-reason`)
        ?.focus();
      return;
    }
    await submission.run(cancelBody(values), onSubmit, {
      onFields: (caught) => {
        const fromServer = cancelServerErrors(caught);
        if (!fromServer) return false;
        setErrors(fromServer);
        return true;
      },
      onStale: (caught) => {
        if (isCode(caught, ...STALE_CODES)) onStale?.();
      },
    });
  }

  const { submitting } = submission;
  let content;
  if (charge.status === 'loading') {
    content = <LoadingState rows={3} />;
  } else if (charge.status === 'error') {
    content = <ErrorAlert error={charge.error} onRetry={charge.onRetry} />;
  } else {
    const proposed =
      net === null
        ? null
        : proposedCreditCents(net, pkg.remaining_units, pkg.units_granted);
    content = (
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        {net === null || proposed === null ? (
          <p className="text-sm">{t('billing.packages.cancel.noCharge')}</p>
        ) : (
          <div className="flex flex-col gap-1" data-cancel-estimate="">
            <p className="text-sm">
              {t('billing.packages.cancel.net', {
                amount: labels.money(centsText(net)),
              })}
            </p>
            <p className="text-sm font-medium">
              {t('billing.packages.cancel.estimate', {
                amount: labels.money(centsText(proposed)),
              })}
            </p>
            <p className="text-sm text-muted-foreground">
              {t('billing.packages.cancel.estimateNote')}
            </p>
          </div>
        )}
        {net !== null ? (
          <CorrectionField
            id={`${id}-credit`}
            label={t('billing.packages.cancel.fields.credit')}
            help={t('billing.packages.cancel.hints.credit', {
              amount: labels.money(centsText(net)),
            })}
            error={errors.credit}
          >
            {(wiring) => (
              <Input
                {...wiring}
                type="text"
                inputMode="decimal"
                autoComplete="off"
                value={values.credit}
                onChange={(event) => change('credit', event.target.value)}
              />
            )}
          </CorrectionField>
        ) : null}
        <CorrectionField
          id={`${id}-reason`}
          label={t('billing.packages.cancel.fields.reason')}
          error={errors.reason}
        >
          {(wiring) => (
            <textarea
              {...wiring}
              rows={2}
              className={TEXTAREA_CLASS}
              maxLength={CANCEL_REASON_MAX}
              value={values.reason}
              onChange={(event) => change('reason', event.target.value)}
            />
          )}
        </CorrectionField>
        <p className="text-sm font-medium">
          {t('billing.packages.cancel.warning')}
        </p>
        <p className="text-sm text-muted-foreground">
          {t('billing.packages.cancel.passwordNote')}
        </p>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.packages.cancel.keep')}
          </Button>
          <Button type="submit" variant="destructive" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.packages.cancel.saving')} showLabel />
            ) : (
              t('billing.packages.cancel.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.packages.cancel.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.packages.cancel.help', {
            name: pkg.product.name,
            student: pkg.student.name,
          })}
        </DialogDescription>
      </DialogHeader>
      <p className="text-sm" data-cancel-remaining="">
        {t('billing.packages.cancel.remaining', {
          remaining: labels.remaining(pkg),
          granted: labels.granted(pkg),
        })}
      </p>
      {submission.failure ? <ErrorAlert error={submission.failure} /> : null}
      {content}
    </DialogContent>
  );
}
