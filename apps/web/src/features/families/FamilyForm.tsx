import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  FAMILY_NAME_MAX_LENGTH,
  familyFormSchema,
  type FamilyFormValues,
  type FamilyMessageKey,
} from './rules';

// The family form (#172 H2), shared by /families/new and
// /families/:id/edit: one name field (React Hook Form + Zod, as #28 J3). A
// server field error lands on the field; version_conflict shows "Reload".
// No API call of its own: the page passes `onSubmit` (rejects with the
// error to show) and `onReload`.

interface FamilyFormProps {
  defaultValues: FamilyFormValues;
  /** Sends the form; rejects with the error to show. */
  onSubmit: (values: FamilyFormValues) => Promise<void>;
  /** Refetches the family after a version conflict (edit only). */
  onReload?: () => Promise<void>;
  /** Where "Cancel" goes. */
  cancelTo: string;
  /** The id of the page heading that names the form. */
  labelledBy: string;
}

export function FamilyForm({
  defaultValues,
  onSubmit,
  onReload,
  cancelTo,
  labelledBy,
}: FamilyFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const {
    register,
    handleSubmit,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<FamilyFormValues>({
    resolver: zodResolver(familyFormSchema),
    defaultValues,
    shouldFocusError: true,
  });

  const submit = handleSubmit(async (values) => {
    setError(null);
    try {
      await onSubmit(values);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.fieldErrors.some((e) => String(e.path[1]) === 'name')
      ) {
        setFieldError('name', { message: 'families.validation.nameRequired' });
        setFocus('name');
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

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const fieldId = `${id}-name`;
  const errorId = `${fieldId}-error`;
  const nameMessage = errors.name?.message
    ? t(errors.name.message as FamilyMessageKey)
    : undefined;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={
            isConflict && onReload
              ? () => {
                  setError(null);
                  void onReload();
                }
              : undefined
          }
          retryLabel={isConflict ? t('families.conflict.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId} className="text-sm font-medium">
            {t('families.fields.name')}
          </label>
          <Input
            id={fieldId}
            type="text"
            autoComplete="off"
            maxLength={FAMILY_NAME_MAX_LENGTH}
            aria-invalid={nameMessage ? true : undefined}
            aria-describedby={nameMessage ? errorId : undefined}
            {...register('name')}
          />
          <FieldError id={errorId} message={nameMessage} />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('families.saving')} showLabel />
            ) : (
              t('families.save')
            )}
          </Button>
          <Link
            to={cancelTo}
            className="inline-flex min-h-6 items-center px-1 text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('families.cancel')}
          </Link>
        </div>
      </form>
    </div>
  );
}
