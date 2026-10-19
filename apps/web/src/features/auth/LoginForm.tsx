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
import { FieldError } from './FieldError';

// The login form (#28 B2–B4, B7), without any API call: LoginPage passes
// `onSubmit`, stories pass a stub. The values live only in this form's state
// (React Hook Form), never in a store, the query cache or a log (B8).

/** The #23 bounds of LoginRequest. */
export const USERNAME_MAX_LENGTH = 256;
export const PASSWORD_MAX_LENGTH = 1024;

// Messages are catalog keys, translated when shown (they follow a language
// switch without losing the typed values).
const loginSchema = z.object({
  username: z
    .string()
    .min(1, 'auth.login.usernameRequired')
    .max(USERNAME_MAX_LENGTH, 'auth.login.tooLong'),
  password: z
    .string()
    .min(1, 'auth.login.passwordRequired')
    .max(PASSWORD_MAX_LENGTH, 'auth.login.tooLong'),
});

export type LoginValues = z.infer<typeof loginSchema>;

type LoginMessageKey =
  | 'auth.login.usernameRequired'
  | 'auth.login.passwordRequired'
  | 'auth.login.tooLong';

interface LoginFormProps {
  /** Sends the login. Rejects with the error to show (an ApiError). */
  onSubmit: (values: LoginValues) => Promise<void>;
  /** Shows the "session expired" notice until the next submit (B7). */
  sessionExpired?: boolean;
}

export function LoginForm({
  onSubmit,
  sessionExpired = false,
}: LoginFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const usernameId = `${id}-username`;
  const passwordId = `${id}-password`;
  const [error, setError] = useState<unknown>(null);
  const [showNotice, setShowNotice] = useState(sessionExpired);
  // One request per submit, even on a double click before the re-render.
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    resetField,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: '', password: '' },
    // Focus moves to the first invalid field on a failed submit (B2).
    shouldFocusError: true,
  });

  const submit = handleSubmit(async (values) => {
    setError(null);
    try {
      await onSubmit(values);
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiError && caught.code === 'invalid_credentials') {
        // Keep the username; clear and focus the password (B4).
        resetField('password');
        setFocus('password');
      }
    }
  });

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    // Never a native submission: credentials never reach the URL.
    event.preventDefault();
    setShowNotice(false);
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    void submit(event).finally(() => {
      inFlight.current = false;
    });
  }

  const usernameError = errors.username?.message as LoginMessageKey | undefined;
  const passwordError = errors.password?.message as LoginMessageKey | undefined;
  const usernameErrorId = `${usernameId}-error`;
  const passwordErrorId = `${passwordId}-error`;

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6 py-6">
      <h1 className="text-2xl font-semibold">{t('auth.login.title')}</h1>
      <div role="status">
        {showNotice ? (
          <p className="rounded-md border border-border p-3">
            {tErrors('session_expired')}
          </p>
        ) : null}
      </div>
      {error ? <ErrorAlert error={error} /> : null}
      {/* method="post": a submission before hydration is never a GET. */}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={usernameId} className="text-sm font-medium">
            {t('auth.login.username')}
          </label>
          <Input
            id={usernameId}
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            aria-invalid={usernameError ? true : undefined}
            aria-describedby={usernameError ? usernameErrorId : undefined}
            {...register('username')}
          />
          <FieldError
            id={usernameErrorId}
            message={usernameError ? t(usernameError) : undefined}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={passwordId} className="text-sm font-medium">
            {t('auth.login.password')}
          </label>
          <Input
            id={passwordId}
            type="password"
            autoComplete="current-password"
            aria-invalid={passwordError ? true : undefined}
            aria-describedby={passwordError ? passwordErrorId : undefined}
            {...register('password')}
          />
          <FieldError
            id={passwordErrorId}
            message={passwordError ? t(passwordError) : undefined}
          />
        </div>
        <Button type="submit" disabled={isSubmitting} className="self-start">
          {isSubmitting ? (
            <Spinner label={t('auth.login.submitting')} showLabel />
          ) : (
            t('auth.login.submit')
          )}
        </Button>
      </form>
    </div>
  );
}
