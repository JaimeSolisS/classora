import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
} from '@/features/auth/redeem/RedeemForm';
import { z } from '@/lib/forms';

// The change-password form (#30 K2), without any API call: the page passes
// `onSubmit`, stories pass a stub. A read-only `username` field holds the
// signed-in username, so password managers update the right entry. The
// passwords live only in this form's state, never in a store, the query
// cache or a log (#28 B8), and the form is emptied after a change.

/** The bound of PasswordChange.current_password. */
const CURRENT_MAX_LENGTH = 1024;

const changeSchema = z
  .object({
    currentPassword: z
      .string()
      .min(1, 'account.password.currentRequired')
      .max(CURRENT_MAX_LENGTH, 'invite.tooLong'),
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

type ChangeValues = z.infer<typeof changeSchema>;

type ChangeMessageKey =
  | 'account.password.currentRequired'
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

const EMPTY: ChangeValues = {
  currentPassword: '',
  newPassword: '',
  confirmPassword: '',
};

export interface PasswordChangeInput {
  currentPassword: string;
  newPassword: string;
}

interface ChangePasswordFormProps {
  /** The signed-in username (read-only field for password managers). */
  username: string;
  /** Changes the password. Rejects with the error to show (an ApiError). */
  onSubmit: (input: PasswordChangeInput) => Promise<void>;
  /** Shows the success status (stories; the form sets it after a change). */
  initiallyDone?: boolean;
}

export function ChangePasswordForm({
  username,
  onSubmit,
  initiallyDone = false,
}: ChangePasswordFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    username: `${id}-username`,
    current: `${id}-current`,
    newPassword: `${id}-new`,
    confirm: `${id}-confirm`,
  };
  const [error, setError] = useState<unknown>(null);
  const [currentInvalid, setCurrentInvalid] = useState(false);
  const [passwordCode, setPasswordCode] = useState<PasswordCode | null>(null);
  const [done, setDone] = useState(initiallyDone);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    resetField,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<ChangeValues>({
    resolver: zodResolver(changeSchema),
    defaultValues: EMPTY,
    shouldFocusError: true,
  });

  const submit = handleSubmit(async (values) => {
    setError(null);
    setCurrentInvalid(false);
    setPasswordCode(null);
    setDone(false);
    try {
      await onSubmit({
        currentPassword: values.currentPassword,
        newPassword: values.newPassword,
      });
      reset(EMPTY);
      setDone(true);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.code === 'current_password_invalid'
      ) {
        setCurrentInvalid(true);
        resetField('currentPassword');
        setFocus('currentPassword');
        return;
      }
      if (caught instanceof ApiError && PASSWORD_CODES.has(caught.code)) {
        setPasswordCode(caught.code as PasswordCode);
        setFocus('newPassword');
        return;
      }
      // rate_limited and anything else: the alert region; the passwords are
      // emptied (the form keeps nothing else).
      reset(EMPTY);
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
    key ? t(key as ChangeMessageKey) : undefined;
  const currentMessage = currentInvalid
    ? tErrors('current_password_invalid')
    : message(errors.currentPassword?.message);
  const newMessage = passwordCode
    ? tErrors(passwordCode)
    : message(errors.newPassword?.message);
  const confirmMessage = message(errors.confirmPassword?.message);
  const hintId = `${ids.newPassword}-hint`;
  const errorId = (field: string) => `${field}-error`;

  return (
    <div className="flex w-full max-w-md flex-col gap-4">
      <div role="status">
        {done ? (
          <p className="rounded-md border border-border p-3">
            {t('account.password.done')}
          </p>
        ) : null}
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
            {t('account.password.username')}
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
          <label htmlFor={ids.current} className="text-sm font-medium">
            {t('account.password.current')}
          </label>
          <Input
            id={ids.current}
            type="password"
            autoComplete="current-password"
            aria-invalid={currentMessage ? true : undefined}
            aria-describedby={currentMessage ? errorId(ids.current) : undefined}
            {...register('currentPassword', {
              onChange: () => setCurrentInvalid(false),
            })}
          />
          <FieldError id={errorId(ids.current)} message={currentMessage} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.newPassword} className="text-sm font-medium">
            {t('account.password.new')}
          </label>
          <Input
            id={ids.newPassword}
            type="password"
            autoComplete="new-password"
            aria-invalid={newMessage ? true : undefined}
            aria-describedby={
              newMessage ? `${hintId} ${errorId(ids.newPassword)}` : hintId
            }
            {...register('newPassword', {
              onChange: () => setPasswordCode(null),
            })}
          />
          <p id={hintId} className="text-sm text-muted-foreground">
            {t('invite.passwordHint')}
          </p>
          <FieldError id={errorId(ids.newPassword)} message={newMessage} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.confirm} className="text-sm font-medium">
            {t('account.password.confirm')}
          </label>
          <Input
            id={ids.confirm}
            type="password"
            autoComplete="new-password"
            aria-invalid={confirmMessage ? true : undefined}
            aria-describedby={confirmMessage ? errorId(ids.confirm) : undefined}
            {...register('confirmPassword')}
          />
          <FieldError id={errorId(ids.confirm)} message={confirmMessage} />
        </div>
        <Button type="submit" disabled={isSubmitting} className="self-start">
          {isSubmitting ? (
            <Spinner label={t('account.password.submitting')} showLabel />
          ) : (
            t('account.password.submit')
          )}
        </Button>
      </form>
    </div>
  );
}
