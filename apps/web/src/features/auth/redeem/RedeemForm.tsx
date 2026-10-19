import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { z } from '@/lib/forms';
import { FieldError } from '../FieldError';

// "Set your password" for a one-time link (#29 J4; #30 J2 for reset links):
// a short intro, the person's username (prominent, selectable, and in a read-only
// `username` field so password managers save the pair), a new password and
// its confirmation. No API call here: the page passes `onSubmit`. The
// passwords live only in this form's state, never in a store, the query
// cache or a log (#28 B8).

/** The password policy bounds (ADR-0007 D6); the server stays the authority. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

const redeemSchema = z
  .object({
    newPassword: z
      .string()
      .min(1, 'invite.passwordRequired')
      .min(PASSWORD_MIN_LENGTH, 'invite.passwordHint')
      .max(PASSWORD_MAX_LENGTH, 'invite.tooLong'),
    confirmPassword: z.string(),
  })
  .refine((values) => values.newPassword === values.confirmPassword, {
    message: 'invite.mismatch',
    path: ['confirmPassword'],
  });

type RedeemValues = z.infer<typeof redeemSchema>;

type RedeemMessageKey =
  | 'invite.passwordRequired'
  | 'invite.passwordHint'
  | 'invite.tooLong'
  | 'invite.mismatch';

/** Server codes shown under the new-password field. */
const PASSWORD_CODES = new Set([
  'password_too_short',
  'password_too_long',
  'password_equals_username',
]);
type PasswordCode =
  'password_too_short' | 'password_too_long' | 'password_equals_username';

interface RedeemFormProps {
  username: string;
  /** The line above the username (already translated). */
  intro: string;
  /** Sends the new password. Rejects with the error to show (an ApiError). */
  onSubmit: (password: string) => Promise<void>;
}

export function RedeemForm({ username, intro, onSubmit }: RedeemFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    username: `${id}-username`,
    newPassword: `${id}-new-password`,
    confirmPassword: `${id}-confirm-password`,
  };
  const [error, setError] = useState<unknown>(null);
  const [passwordCode, setPasswordCode] = useState<PasswordCode | null>(null);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<RedeemValues>({
    resolver: zodResolver(redeemSchema),
    defaultValues: { newPassword: '', confirmPassword: '' },
    shouldFocusError: true,
  });

  const submit = handleSubmit(async (values) => {
    setError(null);
    setPasswordCode(null);
    try {
      await onSubmit(values.newPassword);
    } catch (caught) {
      if (caught instanceof ApiError && PASSWORD_CODES.has(caught.code)) {
        setPasswordCode(caught.code as PasswordCode);
        setFocus('newPassword');
        return;
      }
      setError(caught);
    }
  });

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    void submit(event).finally(() => {
      inFlight.current = false;
    });
  }

  const message = (key: string | undefined) =>
    key ? t(key as RedeemMessageKey) : undefined;
  const newPasswordMessage = passwordCode
    ? tErrors(passwordCode)
    : message(errors.newPassword?.message);
  const confirmMessage = message(errors.confirmPassword?.message);
  const hintId = `${ids.newPassword}-hint`;
  const errorId = (field: string) => `${field}-error`;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <p>{intro}</p>
        <p className="text-xl font-semibold break-all select-all">{username}</p>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.username} className="text-sm font-medium">
            {t('invite.username')}
          </label>
          <Input
            id={ids.username}
            name="username"
            type="text"
            autoComplete="username"
            readOnly
            value={username}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.newPassword} className="text-sm font-medium">
            {t('invite.newPassword')}
          </label>
          <Input
            id={ids.newPassword}
            type="password"
            autoComplete="new-password"
            aria-invalid={newPasswordMessage ? true : undefined}
            aria-describedby={
              newPasswordMessage
                ? `${hintId} ${errorId(ids.newPassword)}`
                : hintId
            }
            {...register('newPassword', {
              onChange: () => setPasswordCode(null),
            })}
          />
          <p id={hintId} className="text-sm text-muted-foreground">
            {t('invite.passwordHint')}
          </p>
          <FieldError
            id={errorId(ids.newPassword)}
            message={newPasswordMessage}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.confirmPassword} className="text-sm font-medium">
            {t('invite.confirmPassword')}
          </label>
          <Input
            id={ids.confirmPassword}
            type="password"
            autoComplete="new-password"
            aria-invalid={confirmMessage ? true : undefined}
            aria-describedby={
              confirmMessage ? errorId(ids.confirmPassword) : undefined
            }
            {...register('confirmPassword')}
          />
          <FieldError
            id={errorId(ids.confirmPassword)}
            message={confirmMessage}
          />
        </div>
        <Button type="submit" disabled={isSubmitting} className="self-start">
          {isSubmitting ? (
            <Spinner label={t('invite.submitting')} showLabel />
          ) : (
            t('invite.submit')
          )}
        </Button>
      </form>
    </div>
  );
}
