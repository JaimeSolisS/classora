import { useId, useRef } from 'react';
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
import { FieldError } from '@/features/auth/FieldError';
import { SHEET_CLASS } from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { Vendor } from '../api';
import {
  initialVendorForm,
  textLength,
  VENDOR_CONTACT_MAX,
  validateVendorForm,
  vendorServerErrors,
  type VendorErrors,
  type VendorField,
  type VendorFormValues,
} from '../forms';
import { RowNoticeAlert } from '../RowNoticeAlert';
import { useRowSave, type RowNotice, type SaveResult } from '../useRowSave';

// Add a vendor, or edit one's name and contact (M7-11b C2, C3, D2), without
// any API call. The contact is optional, multi-line and up to 500 characters,
// with a counter; clearing it on an edit clears it on the server. A taken
// name goes on the field, with the hint that a deactivated vendor of that
// name may exist.

export interface VendorDialogProps {
  open: boolean;
  /** The vendor being edited, as the list has it now; null adds one. */
  vendor: Vendor | null;
  /** Stores `values` based on `base`, the snapshot they were edited from;
   * rejects with the error to show. */
  onSubmit: (
    values: VendorFormValues,
    base: Vendor | null,
  ) => Promise<SaveResult>;
  /** Refetches the list; resolves whether it reloaded. */
  onReload: () => Promise<boolean>;
  /** The dialog closed: `saved` after a stored change. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the errors to start from. */
  initialValues?: VendorFormValues;
  initialErrors?: VendorErrors;
  initialNotice?: RowNotice;
}

export function VendorDialog(props: VendorDialogProps) {
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

const D = 'finance.vendors.dialog';
const FIELD_ORDER: readonly VendorField[] = ['name', 'contact'];

function DialogBody({
  vendor,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors,
  initialNotice,
  busyRef,
}: VendorDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const fieldId = (field: VendorField) => `${id}-${field}`;
  const errorId = (field: VendorField) => `${id}-${field}-error`;
  const ids = {
    hint: `${id}-name-hint`,
    contactHelp: `${id}-contact-help`,
    counter: `${id}-contact-counter`,
  };
  const form = useRowSave({
    row: vendor,
    initial: initialVendorForm,
    initialValues,
    initialErrors,
    initialNotice,
    validate: validateVendorForm,
    serverErrors: vendorServerErrors,
    onSubmit,
    onReload,
    onClose,
    focusFirst: (found) => {
      const first = FIELD_ORDER.find((field) => found[field]);
      if (first) {
        document.getElementById(fieldId(first))?.focus();
      }
    },
    busyRef,
  });
  const { base, values, errors, failure } = form;
  const editing = base !== null;
  const taken = errors.name === 'errors:vendor_exists';
  const describedBy = (field: VendorField, ...extra: string[]) =>
    [...extra, errors[field] ? errorId(field) : ''].filter(Boolean).join(' ') ||
    undefined;
  const change = (patch: Partial<VendorFormValues>) =>
    form.setValues({ ...values, ...patch });

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {editing ? t(`${D}.editTitle`) : t(`${D}.addTitle`)}
        </DialogTitle>
        <DialogDescription>{t(`${D}.help`)}</DialogDescription>
      </DialogHeader>
      <RowNoticeAlert notice={form.notice} kind="vendors" />
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={
            form.conflict && !form.reloading
              ? () => void form.reload()
              : undefined
          }
          retryLabel={form.conflict ? t('finance.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={form.onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('name')} className="text-sm font-medium">
            {t(`${D}.name`)}
          </label>
          <Input
            id={fieldId('name')}
            autoComplete="off"
            className="h-11 md:h-9"
            value={values.name}
            disabled={form.locked}
            aria-invalid={errors.name ? true : undefined}
            aria-describedby={describedBy('name', taken ? ids.hint : '')}
            onChange={(event) => change({ name: event.target.value })}
          />
          <FieldError id={errorId('name')} message={message(errors.name)} />
          {taken ? (
            <p id={ids.hint} className="text-sm" data-exists-hint>
              {t(`${D}.existsHint`)}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('contact')} className="text-sm font-medium">
            {t(`${D}.contact`)}
          </label>
          <textarea
            id={fieldId('contact')}
            rows={3}
            className={TEXTAREA_CLASS}
            value={values.contact}
            disabled={form.locked}
            aria-invalid={errors.contact ? true : undefined}
            aria-describedby={describedBy(
              'contact',
              ids.contactHelp,
              ids.counter,
            )}
            onChange={(event) => change({ contact: event.target.value })}
          />
          <p id={ids.contactHelp} className="text-sm text-muted-foreground">
            {t(`${D}.contactHelp`)}
          </p>
          <p
            id={ids.counter}
            className="text-sm text-muted-foreground"
            data-contact-counter
          >
            {t(`${D}.counter`, {
              length: textLength(values.contact.trim()),
              max: VENDOR_CONTACT_MAX,
            })}
          </p>
          <FieldError
            id={errorId('contact')}
            message={message(errors.contact)}
          />
        </div>
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
            disabled={form.locked}
          >
            {form.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : (
              t('finance.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
