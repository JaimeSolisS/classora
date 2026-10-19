import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent, type RefObject } from 'react';
import { useForm } from 'react-hook-form';
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
import { z } from '@/lib/forms';
import { FieldError } from './FieldError';
import { PASSWORD_MAX_LENGTH } from './LoginForm';

// The password dialog for `403 reauth_required` (#28 D2–D4), without any API
// call: AuthProvider passes `onConfirm`, stories pass a stub.

const reauthSchema = z.object({
  password: z
    .string()
    .min(1, 'auth.reauth.passwordRequired')
    .max(PASSWORD_MAX_LENGTH, 'auth.login.tooLong'),
});

type ReauthValues = z.infer<typeof reauthSchema>;
type ReauthMessageKey = 'auth.reauth.passwordRequired' | 'auth.login.tooLong';

interface ReauthDialogProps {
  open: boolean;
  /** POST /auth/reauthenticate. Rejects with the error to show. */
  onConfirm: (password: string) => Promise<void>;
  /** Escape, the close button or "Cancel". */
  onCancel: () => void;
}

export function ReauthDialog({ open, onConfirm, onCancel }: ReauthDialogProps) {
  const { t } = useTranslation();
  // While the request is pending, nothing cancels: its answer decides.
  const pending = useRef(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !pending.current) {
          onCancel();
        }
      }}
    >
      {open ? (
        <ReauthDialogContent
          onConfirm={onConfirm}
          onCancel={onCancel}
          pendingRef={pending}
          title={t('auth.reauth.title')}
        />
      ) : null}
    </Dialog>
  );
}

// Mounted only while the dialog is open, so every opening starts with an
// empty field and no error.
function ReauthDialogContent({
  onConfirm,
  onCancel,
  pendingRef,
  title,
}: Omit<ReauthDialogProps, 'open'> & {
  pendingRef: RefObject<boolean>;
  title: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const passwordId = `${id}-password`;
  const errorId = `${passwordId}-error`;
  const [error, setError] = useState<unknown>(null);
  // The element focused before the dialog opened (it opens without a
  // trigger), to give focus back on close.
  const returnFocus = useRef<Element | null>(null);
  const {
    register,
    handleSubmit,
    resetField,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<ReauthValues>({
    resolver: zodResolver(reauthSchema),
    defaultValues: { password: '' },
    shouldFocusError: true,
  });

  const submit = handleSubmit(async ({ password }) => {
    setError(null);
    try {
      await onConfirm(password);
    } catch (caught) {
      // reauth_failed, rate_limited and other errors stay in the dialog
      // (D3); a 401 has already closed it.
      setError(caught);
      resetField('password');
      setFocus('password');
    }
  });

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) {
      return;
    }
    pendingRef.current = true;
    void submit(event).finally(() => {
      pendingRef.current = false;
    });
  }

  const fieldError = errors.password?.message as ReauthMessageKey | undefined;

  return (
    <DialogContent
      onOpenAutoFocus={(event) => {
        returnFocus.current = document.activeElement;
        event.preventDefault();
        setFocus('password');
      }}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        const target = returnFocus.current;
        if (target instanceof HTMLElement && target.isConnected) {
          target.focus();
        }
      }}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{t('auth.reauth.description')}</DialogDescription>
      </DialogHeader>
      {error ? <ErrorAlert error={error} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={passwordId} className="text-sm font-medium">
            {t('auth.reauth.password')}
          </label>
          <Input
            id={passwordId}
            type="password"
            autoComplete="current-password"
            aria-invalid={fieldError ? true : undefined}
            aria-describedby={fieldError ? errorId : undefined}
            {...register('password')}
          />
          <FieldError
            id={errorId}
            message={fieldError ? t(fieldError) : undefined}
          />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            {t('auth.reauth.cancel')}
          </Button>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('auth.reauth.confirming')} showLabel />
            ) : (
              t('auth.reauth.confirm')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
