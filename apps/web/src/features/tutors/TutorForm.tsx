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
import {
  DISPLAY_NAME_MAX_LENGTH,
  EMAIL_MAX_LENGTH,
  PHONE_MAX_LENGTH,
} from '@/features/users/admin/ProfileForm';
import { z } from '@/lib/forms';
import type { TutorDetail, TutorField, TutorUpdate } from './api';
import {
  EXPERIENCE_MAX_LENGTH,
  tutorChanges,
  valuesOf,
  type TutorValues,
} from './rules';

// The tutor profile form (#43 E3). It shows only the fields in the server's
// `editable_fields` (a tutor never sees a name input) and sends `version`
// plus only the changed editable fields (`version` 0 for a tutor without a
// stored profile). Without any API call: the page passes `onSubmit` (which
// resolves after the cache holds the new detail), stories pass a stub. The
// fields follow the server values whenever the loaded version changes.

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9 ().-]{5,30}[0-9]$/;

type MessageKey =
  | 'tutors.form.displayNameRequired'
  | 'tutors.form.displayNameTooLong'
  | 'tutors.form.emailInvalid'
  | 'tutors.form.phoneInvalid'
  | 'tutors.form.experienceTooLong';

const optionalPattern = (pattern: RegExp, max: number, message: MessageKey) =>
  z
    .string()
    .trim()
    .refine(
      (value) => value === '' || (value.length <= max && pattern.test(value)),
      message,
    );

const tutorSchema = z.object({
  displayName: z
    .string()
    .trim()
    .min(1, 'tutors.form.displayNameRequired')
    .max(DISPLAY_NAME_MAX_LENGTH, 'tutors.form.displayNameTooLong'),
  email: optionalPattern(
    EMAIL_PATTERN,
    EMAIL_MAX_LENGTH,
    'tutors.form.emailInvalid',
  ),
  phone: optionalPattern(
    PHONE_PATTERN,
    PHONE_MAX_LENGTH,
    'tutors.form.phoneInvalid',
  ),
  professionalExperience: z
    .string()
    .trim()
    .max(EXPERIENCE_MAX_LENGTH, 'tutors.form.experienceTooLong'),
});

type FormField = keyof TutorValues;

/** API field names → form fields and their message (server 422s). */
const SERVER_FIELDS: Record<TutorField, [FormField, MessageKey]> = {
  display_name: ['displayName', 'tutors.form.displayNameRequired'],
  email: ['email', 'tutors.form.emailInvalid'],
  phone: ['phone', 'tutors.form.phoneInvalid'],
  professional_experience: [
    'professionalExperience',
    'tutors.form.experienceTooLong',
  ],
};

const TEXTAREA_CLASS =
  'w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30';

interface TutorFormProps {
  tutor: TutorDetail;
  /** Sends the change; rejects with the error to show. */
  onSubmit: (body: TutorUpdate) => Promise<void>;
  /** Leaves the form without saving. */
  onCancel: () => void;
  /** Refetches the detail after a version conflict. */
  onReload: () => Promise<void>;
}

export function TutorForm({
  tutor,
  onSubmit,
  onCancel,
  onReload,
}: TutorFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    displayName: `${id}-display-name`,
    email: `${id}-email`,
    phone: `${id}-phone`,
    experience: `${id}-experience`,
    help: `${id}-contact-help`,
  };
  const editable = new Set<TutorField>(tutor.editable_fields);
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<TutorValues>({
    resolver: zodResolver(tutorSchema),
    defaultValues: valuesOf(tutor),
    shouldFocusError: true,
  });

  // A new version from the server (a reload after a conflict): the fields
  // show the server values.
  const {
    version,
    display_name: name,
    email,
    phone,
    professional_experience: experience,
  } = tutor;
  useEffect(() => {
    reset({
      displayName: name,
      email: email ?? '',
      phone: phone ?? '',
      professionalExperience: experience ?? '',
    });
  }, [reset, version, name, email, phone, experience]);

  const submit = handleSubmit(async (values) => {
    setError(null);
    try {
      await onSubmit(tutorChanges(tutor, values));
    } catch (caught) {
      if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        let first: FormField | null = null;
        for (const fieldError of caught.fieldErrors) {
          const mapped =
            SERVER_FIELDS[String(fieldError.path[1]) as TutorField];
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
    await onReload();
  }

  const message = (key: string | undefined) =>
    key ? t(key as MessageKey) : undefined;
  const errorId = (field: string) => `${field}-error`;
  const describedBy = (...parts: (string | false)[]) =>
    parts.filter(Boolean).join(' ') || undefined;
  const fieldMessages = {
    displayName: message(errors.displayName?.message),
    email: message(errors.email?.message),
    phone: message(errors.phone?.message),
    experience: message(errors.professionalExperience?.message),
  };
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const hasContact = editable.has('email') || editable.has('phone');

  return (
    <section
      aria-labelledby={ids.title}
      className="flex w-full max-w-xl flex-col gap-4"
    >
      <h2 id={ids.title} className="text-lg font-semibold">
        {t('tutors.form.title')}
      </h2>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict ? () => void reload() : undefined}
          retryLabel={isConflict ? t('tutors.form.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        {editable.has('display_name') ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.displayName} className="text-sm font-medium">
              {t('tutors.profile.displayName')}
            </label>
            <Input
              id={ids.displayName}
              type="text"
              autoComplete="off"
              aria-invalid={fieldMessages.displayName ? true : undefined}
              aria-describedby={
                fieldMessages.displayName ? errorId(ids.displayName) : undefined
              }
              {...register('displayName')}
            />
            <FieldError
              id={errorId(ids.displayName)}
              message={fieldMessages.displayName}
            />
          </div>
        ) : null}
        {hasContact ? (
          <p id={ids.help} className="text-sm text-muted-foreground">
            {t('tutors.form.contactHelp')}
          </p>
        ) : null}
        {editable.has('email') ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.email} className="text-sm font-medium">
              {t('tutors.form.email')}
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
              {...register('email')}
            />
            <FieldError id={errorId(ids.email)} message={fieldMessages.email} />
          </div>
        ) : null}
        {editable.has('phone') ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.phone} className="text-sm font-medium">
              {t('tutors.form.phone')}
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
              {...register('phone')}
            />
            <FieldError id={errorId(ids.phone)} message={fieldMessages.phone} />
          </div>
        ) : null}
        {editable.has('professional_experience') ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.experience} className="text-sm font-medium">
              {t('tutors.form.experience')}
            </label>
            <textarea
              id={ids.experience}
              rows={6}
              maxLength={EXPERIENCE_MAX_LENGTH}
              className={TEXTAREA_CLASS}
              aria-invalid={fieldMessages.experience ? true : undefined}
              aria-describedby={
                fieldMessages.experience ? errorId(ids.experience) : undefined
              }
              {...register('professionalExperience')}
            />
            <FieldError
              id={errorId(ids.experience)}
              message={fieldMessages.experience}
            />
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('tutors.form.saving')} showLabel />
            ) : (
              t('tutors.form.save')
            )}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={isSubmitting}
            onClick={onCancel}
          >
            {t('tutors.form.cancel')}
          </Button>
        </div>
      </form>
    </section>
  );
}
