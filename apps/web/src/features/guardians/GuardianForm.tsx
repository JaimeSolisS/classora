import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState, type FormEvent } from 'react';
import { Controller, useForm, type FieldPath } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  GUARDIANS_QUERY_KEY,
  searchGuardianAccounts,
  type AccountOption,
  type GuardianAccount,
} from './api';
import {
  EMAIL_MAX_LENGTH,
  guardianFormSchema,
  NAME_MAX_LENGTH,
  PHONE_MAX_LENGTH,
  type GuardianFormValues,
  type GuardianMessageKey,
} from './rules';
import { SearchPicker } from './SearchPicker';
import { useAccountLabel } from './useAccountLabel';

// The guardian form (#34 I2), shared by /guardians/new and
// /guardians/:id/edit. React Hook Form + Zod mirror GuardianCreate/Update.
// The Account field is a picker over GET /users?role=guardian (archived
// accounts are listed but disabled); a chosen account shows as a chip with
// "Remove account". guardian_account_unavailable and guardian_account_in_use
// land on the Account field; version_conflict shows "Reload". Without any
// API call of its own except the picker's search: the page passes
// `onSubmit` (rejects with the error to show) and `onReload`.

type MessageKey =
  | GuardianMessageKey
  | 'errors:guardian_account_unavailable'
  | 'errors:guardian_account_in_use';

type FieldName = FieldPath<GuardianFormValues>;

const SERVER_FIELDS: Record<string, [FieldName, MessageKey]> = {
  given_names: ['givenNames', 'guardians.validation.nameRequired'],
  family_names: ['familyNames', 'guardians.validation.nameRequired'],
  email: ['email', 'guardians.validation.emailInvalid'],
  phone: ['phone', 'guardians.validation.phoneInvalid'],
  user_id: ['account', 'errors:guardian_account_unavailable'],
};

interface GuardianFormProps {
  defaultValues: GuardianFormValues;
  /** Sends the form; rejects with the error to show. */
  onSubmit: (values: GuardianFormValues) => Promise<void>;
  /** Refetches the guardian after a version conflict (edit only). */
  onReload?: () => Promise<void>;
  /** Where "Cancel" goes. */
  cancelTo: string;
  /** The id of the page heading that names the form. */
  labelledBy: string;
  /** The account search (stories pass a stub). */
  searchAccounts?: (q: string, signal: AbortSignal) => Promise<AccountOption[]>;
}

export function GuardianForm({
  defaultValues,
  onSubmit,
  onReload,
  cancelTo,
  labelledBy,
  searchAccounts = searchGuardianAccounts,
}: GuardianFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const accountLabel = useAccountLabel();
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const {
    register,
    control,
    handleSubmit,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<GuardianFormValues>({
    resolver: zodResolver(guardianFormSchema),
    defaultValues,
    shouldFocusError: true,
  });

  const submit = handleSubmit(async (values) => {
    setError(null);
    try {
      await onSubmit(values);
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (
          caught.code === 'guardian_account_in_use' ||
          caught.code === 'guardian_account_unavailable'
        ) {
          setFieldError('account', {
            message: `errors:${caught.code}`,
          });
          return;
        }
        let first: FieldName | null = null;
        for (const fieldError of caught.fieldErrors) {
          const mapped = SERVER_FIELDS[String(fieldError.path[1])];
          if (mapped) {
            setFieldError(mapped[0], { message: mapped[1] });
            first ??= mapped[0];
          }
        }
        if (first) {
          if (first !== 'account') setFocus(first);
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
    await onReload?.();
  }

  const message = (key: string | undefined) => {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'guardian_account_in_use')
      : t(key as GuardianMessageKey);
  };
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  function textField(
    name: 'givenNames' | 'familyNames' | 'email' | 'phone',
    label: string,
    options: { type?: string; maxLength?: number; fieldMessage?: string } = {},
  ) {
    const fieldId = `${id}-${name}`;
    const errorId = `${fieldId}-error`;
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId} className="text-sm font-medium">
          {label}
        </label>
        <Input
          id={fieldId}
          type={options.type ?? 'text'}
          autoComplete="off"
          maxLength={options.maxLength}
          aria-invalid={options.fieldMessage ? true : undefined}
          aria-describedby={options.fieldMessage ? errorId : undefined}
          {...register(name)}
        />
        <FieldError id={errorId} message={options.fieldMessage} />
      </div>
    );
  }

  const accountMessage = message(errors.account?.message);
  const accountErrorId = `${id}-account-error`;
  const accountHeadingId = `${id}-account`;

  return (
    <div className="flex w-full max-w-2xl flex-col gap-6">
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict && onReload ? () => void reload() : undefined}
          retryLabel={isConflict ? t('guardians.conflict.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {textField('givenNames', t('guardians.fields.givenNames'), {
            maxLength: NAME_MAX_LENGTH,
            fieldMessage: message(errors.givenNames?.message),
          })}
          {textField('familyNames', t('guardians.fields.familyNames'), {
            maxLength: NAME_MAX_LENGTH,
            fieldMessage: message(errors.familyNames?.message),
          })}
          {textField('email', t('guardians.fields.email'), {
            type: 'email',
            maxLength: EMAIL_MAX_LENGTH,
            fieldMessage: message(errors.email?.message),
          })}
          {textField('phone', t('guardians.fields.phone'), {
            type: 'tel',
            maxLength: PHONE_MAX_LENGTH,
            fieldMessage: message(errors.phone?.message),
          })}
        </div>
        <Controller
          control={control}
          name="account"
          render={({ field }) => (
            <section
              aria-labelledby={accountHeadingId}
              className="flex flex-col gap-3 rounded-md border border-border p-4"
            >
              <h2 id={accountHeadingId} className="text-lg font-semibold">
                {t('guardians.account.label')}
              </h2>
              {field.value ? (
                <div className="flex flex-wrap items-center gap-3">
                  <p
                    data-slot="guardian-account-chip"
                    className="rounded-md border border-border bg-muted px-3 py-1.5 text-sm font-medium break-words"
                    aria-describedby={
                      accountMessage ? accountErrorId : undefined
                    }
                  >
                    {accountLabel(field.value)}
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => field.onChange(null)}
                  >
                    {t('guardians.account.remove')}
                  </Button>
                </div>
              ) : null}
              <SearchPicker<AccountOption>
                label={t('guardians.account.search')}
                help={t('guardians.account.help')}
                queryKey={[...GUARDIANS_QUERY_KEY, 'accounts']}
                search={searchAccounts}
                optionId={(account) => account.id}
                optionLabel={accountLabel}
                optionDisabled={(account) => account.state === 'archived'}
                selectedId={field.value?.id ?? null}
                onSelect={(account) =>
                  field.onChange({
                    id: account.id,
                    username: account.username,
                    display_name: account.display_name,
                    state: account.state,
                  } satisfies GuardianAccount)
                }
                errorMessage={field.value ? undefined : accountMessage}
              />
              {field.value && accountMessage ? (
                <FieldError id={accountErrorId} message={accountMessage} />
              ) : null}
            </section>
          )}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('guardians.saving')} showLabel />
            ) : (
              t('guardians.save')
            )}
          </Button>
          <Link
            to={cancelTo}
            className="inline-flex min-h-6 items-center px-1 text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {t('guardians.cancel')}
          </Link>
        </div>
      </form>
    </div>
  );
}
