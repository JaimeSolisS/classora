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
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import type { PayItem } from './api';
import {
  initialPayItemEnd,
  payItemEndServerError,
  validatePayItemEnd,
} from './payItemForm';
import { isVersionConflict, type PayrollMessage } from './rules';
import { INPUT_CLASS, SHEET_CLASS } from './sheet';

// The end of a recurring item (#454 B5), without any API call. It works from
// one snapshot, as the pay rule dialog does: a newer version replaces it and
// the date and says so, never during a save, and a `409 version_conflict`
// reloads by itself. A date refused for a paid period shows on the field.
// There is no way to clear an end here: the date is required.

export interface PayItemEndDialogProps {
  open: boolean;
  /** The item, as the list has it now. */
  item: PayItem;
  /** Today's date in the center's zone. */
  today: LocalDate;
  /** Stores `effectiveTo` based on `base`; rejects with the error to show. */
  onSubmit: (effectiveTo: string, base: PayItem) => Promise<void>;
  /** Refetches the items after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a stored date. */
  onClose: (saved: boolean) => void;
  /** Stories: the error to start from. */
  initialError?: PayrollMessage;
}

export function PayItemEndDialog(props: PayItemEndDialogProps) {
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

function DialogBody({
  item: server,
  today,
  onSubmit,
  onReload,
  onClose,
  initialError,
  busyRef,
}: PayItemEndDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [item, setItem] = useState(server);
  const [value, setValue] = useState(() => initialPayItemEnd(server, today));
  const [error, setError] = useState<PayrollMessage | undefined>(initialError);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloaded, setReloaded] = useState(false);
  if (!submitting && server.version !== item.version) {
    // Adjusting state while rendering: see PayRuleDialog.
    setItem(server);
    setValue(initialPayItemEnd(server, today));
    setError(undefined);
    setFailure(null);
    setReloaded(true);
  }

  const fieldId = `${id}-date`;
  const errorId = `${id}-date-error`;
  const locked = submitting || reloading;

  async function reload() {
    setReloading(true);
    try {
      await onReload();
    } finally {
      setReloading(false);
    }
  }

  async function submit() {
    const found = validatePayItemEnd(value, item);
    setError(found);
    if (found) {
      document.getElementById(fieldId)?.focus();
      return;
    }
    setFailure(null);
    setReloaded(false);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(value, item);
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = payItemEndServerError(caught);
      if (fromServer) {
        setError(fromServer);
        document.getElementById(fieldId)?.focus();
      } else {
        setFailure(caught);
        if (isVersionConflict(caught)) {
          setSubmitting(false);
          await reload();
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

  const conflict = isVersionConflict(failure);

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('payroll.payItems.endDialog.title')}</DialogTitle>
        <DialogDescription className="[overflow-wrap:anywhere]">
          {t('payroll.payItems.endDialog.help', {
            type: item.pay_item_type.name,
            description: item.description,
            from: formatLocalDate(item.effective_from),
          })}
        </DialogDescription>
      </DialogHeader>
      {reloaded ? (
        <p role="alert" className="text-sm font-medium" data-pay-item-reloaded>
          {t('payroll.payItems.endDialog.conflictReloaded')}
        </p>
      ) : null}
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={conflict && !reloading ? () => void reload() : undefined}
          retryLabel={
            conflict ? t('payroll.payItems.endDialog.reload') : undefined
          }
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId} className="text-sm font-medium">
            {t('payroll.payItems.endDialog.date')}
          </label>
          <Input
            id={fieldId}
            type="date"
            min={item.effective_from}
            className={INPUT_CLASS}
            value={value}
            disabled={locked}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => setValue(event.target.value)}
          />
          <FieldError id={errorId} message={message(error)} />
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
            disabled={locked}
          >
            {submitting ? (
              <Spinner label={t('payroll.saving')} showLabel />
            ) : (
              t('payroll.payItems.endDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
