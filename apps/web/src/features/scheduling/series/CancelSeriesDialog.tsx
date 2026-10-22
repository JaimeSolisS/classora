import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import { FieldError } from '@/features/auth/FieldError';
import { DialogFailure, ScopeFields } from './dialogParts';
import { useFieldMessage, useReturnFocus } from './hooks';
import {
  CANCEL_REASON_MAX_LENGTH,
  cancelServerErrors,
  validateCancel,
  type CancelErrors,
  type CancelValues,
} from './rules';

// "Cancel future occurrences" (#54b C3): the scope (as in "Edit series")
// and a required reason (1-1000 characters once trimmed, with a counter).
// The page sends POST /class-series/{id}/cancellations with the series
// version; a 422 goes on its field, a version conflict offers a reload,
// and series_ended or occurrence_started show in the shared ErrorAlert.

export interface CancelSeriesDialogProps {
  open: boolean;
  /** The series version (a new one starts the form again). */
  version: number;
  defaultFromDate: string;
  onSubmit: (values: CancelValues) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories: values and an outcome to start from. */
  initialValues?: CancelValues;
  initialFailure?: unknown;
}

export function CancelSeriesDialog(props: CancelSeriesDialogProps) {
  const busy = useRef(false);
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={props.open}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          props.onCancel();
        }
      }}
    >
      {props.open ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{t('scheduling.series.cancel.title')}</DialogTitle>
            <DialogDescription>
              {t('scheduling.series.cancel.description')}
            </DialogDescription>
          </DialogHeader>
          <CancelSeriesContent {...props} busyRef={busy} key={props.version} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function CancelSeriesContent({
  defaultFromDate,
  onSubmit,
  onCancel,
  onReload,
  initialValues,
  initialFailure,
  busyRef,
}: CancelSeriesDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const reasonId = `${id}-reason`;
  const errorId = `${id}-reason-error`;
  const counterId = `${id}-reason-counter`;
  const [values, setValues] = useState<CancelValues>(
    () =>
      initialValues ?? {
        mode: 'following',
        fromDate: defaultFromDate,
        reason: '',
      },
  );
  const [errors, setErrors] = useState<CancelErrors>({});
  const [failure, setFailure] = useState<unknown>(initialFailure ?? null);
  const [submitting, setSubmitting] = useState(false);

  function focus(found: CancelErrors) {
    const target = found.fromDate
      ? `${id}-scope-from`
      : found.reason
        ? reasonId
        : undefined;
    if (target) {
      document.getElementById(target)?.focus();
    }
  }

  async function send() {
    setFailure(null);
    const found = validateCancel(values);
    setErrors(found);
    if (found.fromDate || found.reason) {
      focus(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (caught) {
      const mapped =
        caught instanceof ApiError
          ? cancelServerErrors(caught.fieldErrors)
          : {};
      if (mapped.fromDate || mapped.reason) {
        setErrors(mapped);
        focus(mapped);
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
      void send();
    }
  }

  const reasonMessage = message(errors.reason);
  const length = values.reason.trim().length;

  return (
    <form
      noValidate
      onSubmit={onFormSubmit}
      className="flex min-w-0 flex-col gap-4"
    >
      {failure ? <DialogFailure error={failure} onReload={onReload} /> : null}
      <ScopeFields
        idPrefix={`${id}-scope`}
        mode={values.mode}
        fromDate={values.fromDate}
        error={message(errors.fromDate)}
        disabled={submitting}
        onChange={(mode, fromDate) =>
          setValues((current) => ({ ...current, mode, fromDate }))
        }
      />
      <div className="flex flex-col gap-2">
        <label htmlFor={reasonId} className="text-sm font-medium">
          {t('scheduling.series.cancel.reason')}
        </label>
        <textarea
          id={reasonId}
          rows={4}
          required
          value={values.reason}
          aria-invalid={reasonMessage ? true : undefined}
          aria-describedby={[reasonMessage ? errorId : undefined, counterId]
            .filter(Boolean)
            .join(' ')}
          disabled={submitting}
          onChange={(event) =>
            setValues((current) => ({
              ...current,
              reason: event.target.value,
            }))
          }
          className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
        />
        <FieldError id={errorId} message={reasonMessage} />
        <p
          id={counterId}
          className={
            length > CANCEL_REASON_MAX_LENGTH
              ? 'text-sm text-destructive'
              : 'text-sm text-muted-foreground'
          }
        >
          {t('scheduling.override.counter', {
            length,
            max: CANCEL_REASON_MAX_LENGTH,
          })}
        </p>
      </div>
      <p className="rounded-md border border-border p-3 text-sm">
        {t('scheduling.series.cancel.notice')}
      </p>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
        >
          {t('scheduling.series.dialogCancel')}
        </Button>
        <Button type="submit" variant="destructive" disabled={submitting}>
          {submitting ? (
            <Spinner
              label={t('scheduling.series.cancel.submitting')}
              showLabel
            />
          ) : (
            t('scheduling.series.cancel.submit')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
