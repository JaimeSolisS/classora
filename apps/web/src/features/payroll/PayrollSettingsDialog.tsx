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
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { PayrollSettings, ProrationBasis } from './api';
import {
  initialSettingsForm,
  isStaleSettings,
  MAX_SETTINGS_DATE,
  MIN_SETTINGS_DATE,
  PRORATION_BASES,
  settingsServerErrors,
  validateSettingsForm,
  warnsAboutStart,
  type SettingsErrors,
  type SettingsField,
  type SettingsFormValues,
} from './rules';
import { INPUT_CLASS, SELECT_CLASS, SHEET_CLASS } from './sheet';

// "Change payroll settings" (#425 D and the #106 addendum), without any API
// call: the start date (empty keeps payroll off), the Monday anchor and the
// proration basis. A locked field is read-only with a note; the body still
// sends the API's value. Before a date is saved a warning says it can't
// change after the next nightly run and that periods open then, not now.
// `version_conflict` and a lock taken meanwhile reload the settings and say
// so, keeping what was typed; a 422 goes on its field, anything else is an
// alert.

export interface PayrollSettingsDialogProps {
  open: boolean;
  settings: PayrollSettings;
  /** Stores the settings; rejects with the error to show. */
  onSubmit: (values: SettingsFormValues) => Promise<void>;
  /** Refetches the settings after a stale save. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after stored settings. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and errors to start from. */
  initialValues?: SettingsFormValues;
  initialErrors?: SettingsErrors;
}

export function PayrollSettingsDialog(props: PayrollSettingsDialogProps) {
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

const FIELD_ORDER: readonly SettingsField[] = ['startsOn', 'anchor', 'basis'];

function DialogBody({
  settings,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: PayrollSettingsDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<SettingsFormValues>(
    () => initialValues ?? initialSettingsForm(settings),
  );
  const [errors, setErrors] = useState<SettingsErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: SettingsField) => `${id}-${field}`;
  const errorId = (field: SettingsField) => `${id}-${field}-error`;
  const ids = {
    startHelp: `${id}-start-help`,
    startWarning: `${id}-start-warning`,
    anchorHelp: `${id}-anchor-help`,
    basisHelp: `${id}-basis-help`,
  };
  const warning = warnsAboutStart(values, settings);
  const describedBy = (field: SettingsField, ...help: (string | undefined)[]) =>
    [...help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ');
  const focusFirst = (found: SettingsErrors) => {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  };

  async function submit() {
    setFailure(null);
    const found = validateSettingsForm(values, settings);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = settingsServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
        if (isStaleSettings(caught)) {
          await onReload();
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

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('payroll.settings.dialogTitle')}</DialogTitle>
        <DialogDescription>
          {t('payroll.settings.dialogHelp')}
        </DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('startsOn')} className="text-sm font-medium">
            {t('payroll.settings.startsOn')}
          </label>
          <Input
            id={fieldId('startsOn')}
            type="date"
            min={MIN_SETTINGS_DATE}
            max={MAX_SETTINGS_DATE}
            className={INPUT_CLASS}
            readOnly={settings.start_locked}
            value={
              settings.start_locked
                ? (settings.payroll_starts_on ?? '')
                : values.startsOn
            }
            aria-invalid={errors.startsOn ? true : undefined}
            aria-describedby={describedBy(
              'startsOn',
              ids.startHelp,
              warning ? ids.startWarning : undefined,
            )}
            onChange={(event) =>
              setValues({ ...values, startsOn: event.target.value })
            }
          />
          <p id={ids.startHelp} className="text-sm text-muted-foreground">
            {settings.start_locked
              ? t('payroll.settings.startLocked')
              : t('payroll.settings.startHelp')}
          </p>
          {warning ? (
            <p
              id={ids.startWarning}
              className="text-sm font-medium"
              data-payroll-warning
            >
              {t('payroll.settings.startWarning')}
            </p>
          ) : null}
          <FieldError
            id={errorId('startsOn')}
            message={message(errors.startsOn)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('anchor')} className="text-sm font-medium">
            {t('payroll.settings.anchor')}
          </label>
          <Input
            id={fieldId('anchor')}
            type="date"
            min={MIN_SETTINGS_DATE}
            max={MAX_SETTINGS_DATE}
            className={INPUT_CLASS}
            readOnly={settings.anchor_locked}
            value={
              settings.anchor_locked
                ? settings.biweekly_anchor_on
                : values.anchor
            }
            aria-invalid={errors.anchor ? true : undefined}
            aria-describedby={describedBy('anchor', ids.anchorHelp)}
            onChange={(event) =>
              setValues({ ...values, anchor: event.target.value })
            }
          />
          <p id={ids.anchorHelp} className="text-sm text-muted-foreground">
            {settings.anchor_locked
              ? t('payroll.settings.anchorLocked')
              : t('payroll.settings.anchorHelp')}
          </p>
          <FieldError id={errorId('anchor')} message={message(errors.anchor)} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('basis')} className="text-sm font-medium">
            {t('payroll.settings.basis')}
          </label>
          <select
            id={fieldId('basis')}
            className={SELECT_CLASS}
            value={values.basis}
            aria-invalid={errors.basis ? true : undefined}
            aria-describedby={describedBy('basis', ids.basisHelp)}
            onChange={(event) =>
              setValues({
                ...values,
                basis: event.target.value as ProrationBasis,
              })
            }
          >
            {PRORATION_BASES.map((basis) => (
              <option key={basis} value={basis}>
                {t(`payroll.settings.bases.${basis}`)}
              </option>
            ))}
          </select>
          <p id={ids.basisHelp} className="text-sm text-muted-foreground">
            {t('payroll.settings.basisHelp')}
          </p>
          <FieldError id={errorId('basis')} message={message(errors.basis)} />
        </div>
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
              t('payroll.settings.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
