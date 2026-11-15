import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { z } from '@/lib/forms';
import type { UserDetail, UserUpdate } from './api';
import { profileChanges, type ProfileValues } from './rules';

// The profile form of the user detail page (#31 K2): display name, email and
// phone, Save. It mirrors UserUpdate (F1) and sends `version` plus only the
// fields that changed. Without any API call: the page passes `onSubmit`
// (which resolves after the cache holds the new detail), stories pass a stub.
// The form follows the server values whenever the loaded version changes.

/** The F1 bounds and patterns (models.EMAIL_PATTERN / PHONE_PATTERN). */
export const DISPLAY_NAME_MAX_LENGTH = 200;
export const EMAIL_MAX_LENGTH = 254;
export const PHONE_MAX_LENGTH = 32;
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9 ().-]{5,30}[0-9]$/;

type ProfileMessageKey =
  | 'users.profile.displayNameRequired'
  | 'users.profile.displayNameTooLong'
  | 'users.profile.emailInvalid'
  | 'users.profile.phoneInvalid';

const optionalPattern = (
  pattern: RegExp,
  max: number,
  message: ProfileMessageKey,
) =>
  z
    .string()
    .trim()
    .refine(
      (value) => value === '' || (value.length <= max && pattern.test(value)),
      message,
    );

const profileSchema = z.object({
  displayName: z
    .string()
    .trim()
    .min(1, 'users.profile.displayNameRequired')
    .max(DISPLAY_NAME_MAX_LENGTH, 'users.profile.displayNameTooLong'),
  email: optionalPattern(
    EMAIL_PATTERN,
    EMAIL_MAX_LENGTH,
    'users.profile.emailInvalid',
  ),
  phone: optionalPattern(
    PHONE_PATTERN,
    PHONE_MAX_LENGTH,
    'users.profile.phoneInvalid',
  ),
});

type ProfileField = keyof ProfileValues;

/** API field names → form fields and their message (server 422s). */
const SERVER_FIELDS: Record<string, [ProfileField, ProfileMessageKey]> = {
  display_name: ['displayName', 'users.profile.displayNameRequired'],
  email: ['email', 'users.profile.emailInvalid'],
  phone: ['phone', 'users.profile.phoneInvalid'],
};

function valuesOf(user: UserDetail): ProfileValues {
  return {
    displayName: user.display_name,
    email: user.email ?? '',
    phone: user.phone ?? '',
  };
}

interface ProfileFormProps {
  user: UserDetail;
  /** Sends the change; rejects with the error to show. */
  onSubmit: (body: UserUpdate) => Promise<void>;
  /** Refetches the detail after a version conflict. */
  onReload: () => Promise<void>;
}

export function ProfileForm({ user, onSubmit, onReload }: ProfileFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    displayName: `${id}-display-name`,
    email: `${id}-email`,
    phone: `${id}-phone`,
    help: `${id}-contact-help`,
  };
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: valuesOf(user),
    shouldFocusError: true,
  });

  // A new version from the server (a save, a reload, a state change): the
  // fields show the server values.
  const { version, display_name: name, email, phone } = user;
  useEffect(() => {
    reset({ displayName: name, email: email ?? '', phone: phone ?? '' });
  }, [reset, version, name, email, phone]);

  const submit = handleSubmit(async (values) => {
    setError(null);
    setSaved(false);
    try {
      await onSubmit(profileChanges(user, values));
      setSaved(true);
    } catch (caught) {
      if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        let first: ProfileField | null = null;
        for (const fieldError of caught.fieldErrors) {
          const mapped = SERVER_FIELDS[String(fieldError.path[1])];
          if (mapped) {
            setFieldError(mapped[0], { message: mapped[1] });
            first ??= mapped[0];
          }
        }
        if (first) {
          setFocus(first);
          return;
        }
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

  async function reload() {
    setError(null);
    setSaved(false);
    await onReload();
  }

  const message = (key: string | undefined) =>
    key ? t(key as ProfileMessageKey) : undefined;
  const errorId = (field: string) => `${field}-error`;
  const describedBy = (...parts: (string | false)[]) =>
    parts.filter(Boolean).join(' ') || undefined;
  const fieldMessages = {
    displayName: message(errors.displayName?.message),
    email: message(errors.email?.message),
    phone: message(errors.phone?.message),
  };
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  return (
    <section
      aria-labelledby={ids.title}
      className="flex w-full max-w-md flex-col gap-4"
    >
      <h2 id={ids.title} className="text-lg font-semibold">
        {t('users.profile.title')}
      </h2>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict ? () => void reload() : undefined}
          retryLabel={isConflict ? t('users.conflict.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.displayName} className="text-sm font-medium">
            {t('users.profile.displayName')}
          </label>
          <Input
            id={ids.displayName}
            type="text"
            autoComplete="off"
            aria-invalid={fieldMessages.displayName ? true : undefined}
            aria-describedby={
              fieldMessages.displayName ? errorId(ids.displayName) : undefined
            }
            {...register('displayName', { onChange: () => setSaved(false) })}
          />
          <FieldError
            id={errorId(ids.displayName)}
            message={fieldMessages.displayName}
          />
        </div>
        <p id={ids.help} className="text-sm text-muted-foreground">
          {t('users.profile.contactHelp')}
        </p>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.email} className="text-sm font-medium">
            {t('users.profile.email')}
          </label>
          <Input
            id={ids.email}
            type="email"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={fieldMessages.email ? true : undefined}
            aria-describedby={describedBy(
              ids.help,
              Boolean(fieldMessages.email) && errorId(ids.email),
            )}
            {...register('email', { onChange: () => setSaved(false) })}
          />
          <FieldError id={errorId(ids.email)} message={fieldMessages.email} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.phone} className="text-sm font-medium">
            {t('users.profile.phone')}
          </label>
          <Input
            id={ids.phone}
            type="tel"
            autoComplete="off"
            aria-invalid={fieldMessages.phone ? true : undefined}
            aria-describedby={describedBy(
              ids.help,
              Boolean(fieldMessages.phone) && errorId(ids.phone),
            )}
            {...register('phone', { onChange: () => setSaved(false) })}
          />
          <FieldError id={errorId(ids.phone)} message={fieldMessages.phone} />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('users.profile.saving')} showLabel />
            ) : (
              t('users.profile.save')
            )}
          </Button>
          <p role="status" className="text-sm">
            {saved ? t('users.profile.saved') : null}
          </p>
        </div>
      </form>
    </section>
  );
}

/** The profile as text, for a user the caller may not edit. */
export function ProfileReadOnly({ user }: { user: UserDetail }) {
  const { t } = useTranslation();
  const id = useId();
  const notSet = t('users.profile.notSet');
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('users.profile.title')}
      </h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        <dt className="font-medium">{t('users.profile.displayName')}</dt>
        <dd className="min-w-0 break-words">{user.display_name}</dd>
        <dt className="font-medium">{t('users.profile.email')}</dt>
        <dd className="min-w-0 break-words">{user.email ?? notSet}</dd>
        <dt className="font-medium">{t('users.profile.phone')}</dt>
        <dd className="min-w-0 break-words">{user.phone ?? notSet}</dd>
      </dl>
    </section>
  );
}
