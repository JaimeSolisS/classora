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
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { returnReasonError } from './periodRules';
import { serverMessage } from './rules';

// "Send back" a prepared period (#110b D5), without any call: a required
// reason, 1 to 1000 characters once trimmed (counted as the API counts them,
// by code point), and a note that it goes back to Finance. No key and no
// password: it changes no money, and a second click gets a 409 (#110 E7). A
// 422 on the reason stays on the field; anything else is the page's to show,
// so `onSubmit` closes the dialog itself then.

export interface ReturnPeriodDialogProps {
  open: boolean;
  /** Sends the period back; rejects with a 422 to show on the field. */
  onSubmit: (reason: string) => Promise<void>;
  onClose: () => void;
}

export function ReturnPeriodDialog(props: ReturnPeriodDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open ? <Body {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

function Body({
  onSubmit,
  onClose,
  busyRef,
}: ReturnPeriodDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | undefined>();
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const ids = {
    reason: `${id}-reason`,
    help: `${id}-help`,
    error: `${id}-error`,
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    setFailure(null);
    const found = returnReasonError(reason);
    setError(found);
    if (found) {
      document.getElementById(ids.reason)?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(reason.trim());
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 422) {
        setError(serverMessage(caught.fieldErrors[0]?.code ?? caught.code));
        document.getElementById(ids.reason)?.focus();
      } else {
        setFailure(caught);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('payroll.returnDialog.title')}</DialogTitle>
        <DialogDescription>{t('payroll.returnDialog.help')}</DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.reason} className="text-sm font-medium">
            {t('payroll.returnDialog.reason')}
          </label>
          <textarea
            id={ids.reason}
            rows={3}
            className={TEXTAREA_CLASS}
            value={reason}
            required
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${ids.help} ${ids.error}` : ids.help}
            onChange={(event) => {
              setReason(event.target.value);
              setError(undefined);
            }}
          />
          <p id={ids.help} className="text-sm text-muted-foreground">
            {t('payroll.returnDialog.reasonHelp')}
          </p>
          <FieldError id={ids.error} message={message(error)} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
            onClick={onClose}
          >
            {t('payroll.returnDialog.cancel')}
          </Button>
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
          >
            {submitting ? (
              <Spinner label={t('payroll.returnDialog.submitting')} showLabel />
            ) : (
              t('payroll.returnDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
