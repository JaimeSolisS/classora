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
import { ApiError } from '@/app/api';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import {
  DUE_OFFSET_MAX,
  offsetError,
  offsetServerError,
  type DueDateMessage,
} from './rules';

// "Extra days to pay" (#382 C1, C2), without any API call: a whole number
// 0–60 and the hint that it applies to charges posted from now on (#97
// N6). `onSubmit` sends the number (the page adds the summary's version);
// the dialog stays open, busy, until it resolves. A 422 goes on the field;
// `409 version_conflict` says the account changed and calls `onConflict`
// (the page refetches the summary, so the next save sends the new
// version); anything else is an alert. Nothing typed is lost.

export interface DueOffsetDialogProps {
  open: boolean;
  /** The family's offset now. */
  current: number;
  /** Stores the offset; rejects with the error to show. */
  onSubmit: (days: number) => Promise<void>;
  /** A version conflict: refetch the account. */
  onConflict: () => void;
  onClose: () => void;
  /** Stories: the error to start from. */
  initialFailure?: unknown;
}

export function DueOffsetDialog(props: DueOffsetDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

function DialogBody({
  current,
  onSubmit,
  onConflict,
  onClose,
  initialFailure,
  busyRef,
}: DueOffsetDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [text, setText] = useState(String(current));
  const [fieldError, setFieldError] = useState<DueDateMessage | undefined>();
  const [failure, setFailure] = useState<unknown>(initialFailure ?? null);
  const [submitting, setSubmitting] = useState(false);
  const ids = {
    days: `${id}-days`,
    hint: `${id}-hint`,
    error: `${id}-error`,
  };

  async function submit() {
    setFailure(null);
    const found = offsetError(text);
    setFieldError(found);
    if (found) {
      document.getElementById(ids.days)?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(Number(text.trim()));
      busyRef.current = false;
      onClose();
      return;
    } catch (caught) {
      const onField = offsetServerError(caught);
      if (onField) {
        setFieldError(onField);
        window.setTimeout(() => document.getElementById(ids.days)?.focus(), 0);
      } else {
        setFailure(caught);
        if (caught instanceof ApiError && caught.code === 'version_conflict') {
          onConflict();
        }
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    void submit();
  }

  const conflict =
    failure instanceof ApiError && failure.code === 'version_conflict';
  const fieldMessage = message(fieldError);

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-md [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.dueDates.offset.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.dueDates.offset.help')}
        </DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          message={conflict ? t('billing.dueDates.offset.conflict') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <div className="flex max-w-xs flex-col gap-2">
          <label htmlFor={ids.days} className="text-sm font-medium">
            {t('billing.dueDates.offset.field')}
          </label>
          <Input
            id={ids.days}
            type="number"
            inputMode="numeric"
            min={0}
            max={DUE_OFFSET_MAX}
            step={1}
            value={text}
            aria-invalid={fieldMessage ? true : undefined}
            aria-describedby={[ids.hint, fieldMessage ? ids.error : null]
              .filter(Boolean)
              .join(' ')}
            onChange={(event) => setText(event.target.value)}
          />
          <p id={ids.hint} className="text-sm text-muted-foreground">
            {t('billing.dueDates.offset.hint')}
          </p>
          <FieldError id={ids.error} message={fieldMessage} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.dueDates.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.dueDates.saving')} showLabel />
            ) : (
              t('billing.dueDates.offset.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
