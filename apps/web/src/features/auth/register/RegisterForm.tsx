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
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../redeem/RedeemForm';

// The self-registration form (#169 F3), without any API call: the page
// passes `onSubmit`, stories pass a stub. The role is a radio group with
// nothing pre-selected. The passwords live only in this form's state, never
// in a store, the query cache or a log (#28 B8).

/** The #169 bounds of RegistrationCreate. */
export const USERNAME_MAX_LENGTH = 256;
export const DISPLAY_NAME_MAX_LENGTH = 200;

export type RegistrationRole = 'guardian' | 'student';

export interface RegistrationInput {
  username: string;
  display_name: string;
  password: string;
  role: RegistrationRole;
}

const ROLES: readonly RegistrationRole[] = ['guardian', 'student'];

const registerSchema = z
  .object({
    role: z.enum(['guardian', 'student'], 'register.roleRequired'),
    displayName: z
      .string()
      .trim()
      .min(1, 'register.displayNameRequired')
      .max(DISPLAY_NAME_MAX_LENGTH, 'register.tooLong'),
    username: z
      .string()
      .trim()
      .min(1, 'register.usernameRequired')
      .max(USERNAME_MAX_LENGTH, 'register.tooLong'),
    password: z
      .string()
      .min(1, 'invite.passwordRequired')
      .min(PASSWORD_MIN_LENGTH, 'invite.passwordHint')
      .max(PASSWORD_MAX_LENGTH, 'invite.tooLong'),
    confirmPassword: z.string(),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: 'invite.mismatch',
    path: ['confirmPassword'],
  });

type RegisterFields = z.input<typeof registerSchema>;
type RegisterValues = z.output<typeof registerSchema>;

type RegisterMessageKey =
  | 'register.roleRequired'
  | 'register.displayNameRequired'
  | 'register.usernameRequired'
  | 'register.tooLong'
  | 'invite.passwordRequired'
  | 'invite.passwordHint'
  | 'invite.tooLong'
  | 'invite.mismatch';

/** Server codes shown under the username field instead of the alert. */
const USERNAME_CODES = new Set(['username_invalid', 'username_taken']);
type UsernameCode = 'username_invalid' | 'username_taken';
/** Server codes shown under the password field. */
const PASSWORD_CODES = new Set([
  'password_too_short',
  'password_too_long',
  'password_equals_username',
]);
type PasswordCode =
  'password_too_short' | 'password_too_long' | 'password_equals_username';

interface RegisterFormProps {
  /** Sends the request. Rejects with the error to show (an ApiError). */
  onSubmit: (input: RegistrationInput) => Promise<void>;
}

export function RegisterForm({ onSubmit }: RegisterFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    role: `${id}-role`,
    displayName: `${id}-display-name`,
    username: `${id}-username`,
    password: `${id}-password`,
    confirmPassword: `${id}-confirm-password`,
  };
  const [error, setError] = useState<unknown>(null);
  const [usernameCode, setUsernameCode] = useState<UsernameCode | null>(null);
  const [passwordCode, setPasswordCode] = useState<PasswordCode | null>(null);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<RegisterFields, unknown, RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      displayName: '',
      username: '',
      password: '',
      confirmPassword: '',
    },
    shouldFocusError: true,
  });

  const submit = handleSubmit(async (values) => {
    setError(null);
    setUsernameCode(null);
    setPasswordCode(null);
    try {
      await onSubmit({
        username: values.username,
        display_name: values.displayName,
        password: values.password,
        role: values.role,
      });
    } catch (caught) {
      if (caught instanceof ApiError && USERNAME_CODES.has(caught.code)) {
        setUsernameCode(caught.code as UsernameCode);
        setFocus('username');
        return;
      }
      if (caught instanceof ApiError && PASSWORD_CODES.has(caught.code)) {
        setPasswordCode(caught.code as PasswordCode);
        setFocus('password');
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
    key ? t(key as RegisterMessageKey) : undefined;
  const roleMessage = message(errors.role?.message);
  const displayNameMessage = message(errors.displayName?.message);
  const usernameMessage = usernameCode
    ? tErrors(usernameCode)
    : message(errors.username?.message);
  const passwordMessage = passwordCode
    ? tErrors(passwordCode)
    : message(errors.password?.message);
  const confirmMessage = message(errors.confirmPassword?.message);
  const errorId = (field: string) => `${field}-error`;
  const usernameHintId = `${ids.username}-hint`;
  const passwordHintId = `${ids.password}-hint`;
  const describedBy = (...parts: (string | false)[]) =>
    parts.filter(Boolean).join(' ') || undefined;

  return (
    <div className="flex flex-col gap-4">
      {error ? <ErrorAlert error={error} /> : null}
      {/* method="post": a submission before hydration is never a GET. */}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <fieldset
          className="flex flex-col gap-2"
          aria-describedby={roleMessage ? errorId(ids.role) : undefined}
        >
          <legend className="mb-2 text-sm font-medium">
            {t('register.role.legend')}
          </legend>
          {ROLES.map((role) => (
            <label
              key={role}
              htmlFor={`${ids.role}-${role}`}
              className="flex min-h-6 items-center gap-2"
            >
              <input
                id={`${ids.role}-${role}`}
                type="radio"
                value={role}
                className="size-4 accent-primary outline-none focus-visible:ring-3 focus-visible:ring-ring"
                aria-invalid={roleMessage ? true : undefined}
                {...register('role')}
              />
              {t(`register.role.${role}`)}
            </label>
          ))}
          <FieldError id={errorId(ids.role)} message={roleMessage} />
        </fieldset>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.displayName} className="text-sm font-medium">
            {t('register.displayName')}
          </label>
          <Input
            id={ids.displayName}
            type="text"
            autoComplete="name"
            aria-invalid={displayNameMessage ? true : undefined}
            aria-describedby={
              displayNameMessage ? errorId(ids.displayName) : undefined
            }
            {...register('displayName')}
          />
          <FieldError
            id={errorId(ids.displayName)}
            message={displayNameMessage}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.username} className="text-sm font-medium">
            {t('register.username')}
          </label>
          <Input
            id={ids.username}
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            aria-invalid={usernameMessage ? true : undefined}
            aria-describedby={describedBy(
              usernameHintId,
              Boolean(usernameMessage) && errorId(ids.username),
            )}
            {...register('username', {
              onChange: () => setUsernameCode(null),
            })}
          />
          <p id={usernameHintId} className="text-sm text-muted-foreground">
            {t('common.usernameHint')}
          </p>
          <FieldError id={errorId(ids.username)} message={usernameMessage} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.password} className="text-sm font-medium">
            {t('register.password')}
          </label>
          <Input
            id={ids.password}
            type="password"
            autoComplete="new-password"
            aria-invalid={passwordMessage ? true : undefined}
            aria-describedby={describedBy(
              passwordHintId,
              Boolean(passwordMessage) && errorId(ids.password),
            )}
            {...register('password', {
              onChange: () => setPasswordCode(null),
            })}
          />
          <p id={passwordHintId} className="text-sm text-muted-foreground">
            {t('invite.passwordHint')}
          </p>
          <FieldError id={errorId(ids.password)} message={passwordMessage} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.confirmPassword} className="text-sm font-medium">
            {t('register.confirmPassword')}
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
            <Spinner label={t('register.submitting')} showLabel />
          ) : (
            t('register.submit')
          )}
        </Button>
      </form>
    </div>
  );
}
