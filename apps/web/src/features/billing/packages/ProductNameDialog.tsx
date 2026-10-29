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
import { isVersionConflict, nameError, nameServerError } from './rules';

// "Edit" on a product page (#377 B3): the name only (unit, quantity,
// validity and subject are fixed, #94 L7). `onSubmit` resolves once it is
// stored. A 409 version_conflict shows the shared stale-data message,
// keeps the dialog open and reloads the product (`onStale`), so the next
// submit sends the new version; a 422 on the name goes under it.

export interface ProductNameDialogProps {
  open: boolean;
  name: string;
  onSubmit: (name: string) => Promise<void>;
  /** The product was found stale (409 version_conflict). */
  onStale: () => void;
  onClose: (saved: boolean) => void;
}

export function ProductNameDialog(props: ProductNameDialogProps) {
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
  name: initialName,
  onSubmit,
  onStale,
  onClose,
  busyRef,
}: ProductNameDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const ids = { name: `${id}-name`, error: `${id}-name-error` };
  const [name, setName] = useState(initialName);
  const [error, setError] = useState<string | undefined>();
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    setFailure(null);
    const found = nameError(name);
    setError(found);
    if (found) {
      document.getElementById(ids.name)?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(name.trim());
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const onName = nameServerError(caught);
      if (onName) {
        setError(onName);
        document.getElementById(ids.name)?.focus();
      } else {
        setFailure(caught);
        if (isVersionConflict(caught)) {
          onStale();
        }
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void submit();
    }
  }

  return (
    <DialogContent
      className="sm:max-w-lg"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.packages.product.editTitle')}</DialogTitle>
        <DialogDescription>
          {t('billing.packages.products.fixedHint')}
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
          <label htmlFor={ids.name} className="text-sm font-medium">
            {t('billing.packages.fields.name')}
          </label>
          <Input
            id={ids.name}
            type="text"
            autoComplete="off"
            maxLength={200}
            value={name}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? ids.error : undefined}
            onChange={(event) => setName(event.target.value)}
          />
          <FieldError id={ids.error} message={message(error)} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={() => onClose(false)}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.prices.saving')} showLabel />
            ) : (
              t('billing.packages.product.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
