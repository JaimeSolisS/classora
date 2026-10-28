import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import type { LocalDate } from '@/lib/time';
import type { CustomPrice } from './api';
import { useCustomPriceLabels } from './labels';
import { endMinDate, endServerError, validateEndDate } from './rules';

// "End" a custom price (#367 E5): the last day it applies (default and
// minimum today, or its start when that is later). A 422
// custom_price_expiry_invalid (charges already use it after that date)
// goes on the date; a 409 version_conflict offers "Reload".

export interface EndCustomPriceDialogProps {
  item: CustomPrice | null;
  today: LocalDate;
  /** Ends the custom price on `date`; rejects with the error to show. */
  onSubmit: (item: CustomPrice, date: string) => Promise<void>;
  onReload: () => void;
  onClose: () => void;
  /** Stories: the error to start from. */
  initialError?: string;
}

export function EndCustomPriceDialog(props: EndCustomPriceDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.item !== null}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.item ? (
        <DialogBody {...props} item={props.item} busyRef={busy} />
      ) : null}
    </Dialog>
  );
}

function DialogBody({
  item,
  today,
  onSubmit,
  onReload,
  onClose,
  initialError,
  busyRef,
}: EndCustomPriceDialogProps & {
  item: CustomPrice;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const labels = useCustomPriceLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const min = endMinDate(item, today);
  const [date, setDate] = useState<string>(min);
  const [error, setError] = useState<string | undefined>(initialError);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const ids = { date: `${id}-date`, help: `${id}-help`, error: `${id}-error` };

  async function submit() {
    setFailure(null);
    const found = validateEndDate(date, min);
    setError(found);
    if (found) {
      document.getElementById(ids.date)?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(item, date);
    } catch (caught) {
      const onDate = endServerError(caught);
      if (onDate) {
        setError(onDate);
        document.getElementById(ids.date)?.focus();
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

  const conflict =
    failure instanceof ApiError && failure.code === 'version_conflict';
  const target = labels.target(item);

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.customPrices.endDialog.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.customPrices.endDialog.description', {
            name: target.name,
            service: labels.service(item.service_price),
          })}
        </DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={
            conflict
              ? () => {
                  setFailure(null);
                  onReload();
                }
              : undefined
          }
          retryLabel={conflict ? t('billing.prices.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.date} className="text-sm font-medium">
            {t('billing.customPrices.fields.expiresOn')}
          </label>
          <Input
            id={ids.date}
            type="date"
            min={min}
            value={date}
            aria-invalid={error ? true : undefined}
            aria-describedby={
              [ids.help, error ? ids.error : undefined]
                .filter(Boolean)
                .join(' ') || undefined
            }
            onChange={(event) => setDate(event.target.value)}
          />
          <p id={ids.help} className="text-sm text-muted-foreground">
            {t('billing.customPrices.endDialog.help')}
          </p>
          <FieldError id={ids.error} message={message(error)} />
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
              t('billing.customPrices.endDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
