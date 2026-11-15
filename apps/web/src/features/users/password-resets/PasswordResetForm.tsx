import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { z } from '@/lib/forms';

// The reset form (#30 I2, I3), without any API call: the page passes
// `onSubmit`, stories pass a stub. If the typed username is the signed-in
// user's own, the form points to "Change password" and does not submit (UX
// only: the server refuses a self-reset too).

/** The bound of PasswordResetCreate.username. */
export const USERNAME_MAX_LENGTH = 256;

const resetSchema = z.object({
  username: z
    .string()
    .trim()
    .min(1, 'passwordResets.form.usernameRequired')
    .max(USERNAME_MAX_LENGTH, 'passwordResets.form.tooLong'),
});

type ResetValues = z.infer<typeof resetSchema>;

type ResetMessageKey =
  'passwordResets.form.usernameRequired' | 'passwordResets.form.tooLong';

/** The comparable form of a username (the API normalizes the same way). */
function normalized(username: string): string {
  return username.trim().toLowerCase();
}

/** Server answers shown under the username field. */
type UsernameProblem = 'not_found' | 'user_not_active';

interface PasswordResetFormProps {
  /** The signed-in user's username (for the self hint). */
  ownUsername: string;
  /** Issues the reset. Rejects with the error to show (an ApiError). */
  onSubmit: (username: string) => Promise<void>;
}

export function PasswordResetForm({
  ownUsername,
  onSubmit,
}: PasswordResetFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const usernameId = `${id}-username`;
  const helpId = `${id}-help`;
  const selfId = `${id}-self`;
  const errorId = `${usernameId}-error`;
  const [error, setError] = useState<unknown>(null);
  const [problem, setProblem] = useState<UsernameProblem | null>(null);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    setFocus,
    control,
    formState: { errors, isSubmitting },
  } = useForm<ResetValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { username: '' },
    shouldFocusError: true,
  });
  const typed = useWatch({ control, name: 'username' });
  const isSelf =
    normalized(typed) !== '' && normalized(typed) === normalized(ownUsername);

  const submit = handleSubmit(async (values) => {
    setError(null);
    setProblem(null);
    if (normalized(values.username) === normalized(ownUsername)) {
      // The hint is already shown; no request (I2).
      setFocus('username');
      return;
    }
    try {
      await onSubmit(values.username);
      reset();
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.code === 'not_found' || caught.code === 'user_not_active')
      ) {
        setProblem(caught.code);
        setFocus('username');
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

  const usernameMessage =
    problem === 'not_found'
      ? t('passwordResets.form.notFound')
      : problem === 'user_not_active'
        ? tErrors('user_not_active')
        : errors.username?.message
          ? t(errors.username.message as ResetMessageKey)
          : undefined;
  const describedBy = [
    helpId,
    isSelf ? selfId : null,
    usernameMessage ? errorId : null,
  ]
    .filter(Boolean)
    .join(' ');
  const alertMessage =
    error instanceof ApiError && error.code === 'permission_denied'
      ? t('passwordResets.form.notAllowed')
      : undefined;

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex w-full max-w-md flex-col gap-4"
    >
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('passwordResets.form.title')}
      </h2>
      {error ? <ErrorAlert error={error} message={alertMessage} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={usernameId} className="text-sm font-medium">
            {t('passwordResets.form.username')}
          </label>
          <Input
            id={usernameId}
            type="text"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            aria-invalid={usernameMessage ? true : undefined}
            aria-describedby={describedBy}
            {...register('username', { onChange: () => setProblem(null) })}
          />
          <p id={helpId} className="text-sm text-muted-foreground">
            {t('passwordResets.form.help')}
          </p>
          {isSelf ? (
            <p id={selfId} className="text-sm">
              {t('passwordResets.form.self')}{' '}
              <Link
                to="/account/password"
                className="inline-flex min-h-6 items-center font-medium underline underline-offset-4"
              >
                {t('account.password.link')}
              </Link>
            </p>
          ) : null}
          <FieldError id={errorId} message={usernameMessage} />
        </div>
        <Button type="submit" disabled={isSubmitting} className="self-start">
          {isSubmitting ? (
            <Spinner label={t('passwordResets.form.submitting')} showLabel />
          ) : (
            t('passwordResets.form.submit')
          )}
        </Button>
      </form>
    </section>
  );
}
