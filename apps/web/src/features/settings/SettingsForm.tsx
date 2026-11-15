import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Controller, useForm, useWatch, type FieldPath } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import {
  ADDRESS_MAX_LENGTH,
  CENTER_NAME_MAX_LENGTH,
  EMAIL_MAX_LENGTH,
  PHONE_MAX_LENGTH,
  settingsFormSchema,
  TIME_STEP_SECONDS,
  WEEKDAY_KEYS,
  WEEKDAYS,
  zoneOptions,
  type SettingsFormValues,
  type SettingsMessageKey,
} from './rules';

// The business settings form (#37 H3, H4): three fieldsets in one form
// (React Hook Form + Zod mirroring BusinessSettingsUpdate). Brand: name,
// phone, email, address. Operating hours: Monday to Sunday, each with an
// "Open" checkbox and two time inputs (15-minute step) disabled while the
// day is closed. Time zone: a select of Mexico's zones plus the stored one.
// Server field errors land on their fields; tz_change_requires_migration,
// version_conflict ("Reload") and other errors show in the alert region.
// "Saved." is announced in a polite status after a save. No API call of its
// own: the page passes `onSubmit` (rejects with the error to show) and
// `onReload`; a new `defaultValues` (after a save or a reload) resets it.

type FieldName = FieldPath<SettingsFormValues>;

const SERVER_FIELDS: Record<string, [FieldName, SettingsMessageKey]> = {
  center_name: ['centerName', 'settings.validation.nameRequired'],
  contact_phone: ['phone', 'settings.validation.phoneInvalid'],
  contact_email: ['email', 'settings.validation.emailInvalid'],
  address: ['address', 'settings.validation.addressTooLong'],
};

interface SettingsFormProps {
  /** The stored settings as form values (a new object resets the form). */
  defaultValues: SettingsFormValues;
  /** Sends the form; rejects with the error to show. */
  onSubmit: (values: SettingsFormValues) => Promise<void>;
  /** Refetches the settings after a version conflict. */
  onReload: () => Promise<void>;
  /** The id of the page heading that names the form. */
  labelledBy: string;
}

export function SettingsForm({
  defaultValues,
  onSubmit,
  onReload,
  labelledBy,
}: SettingsFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const inFlight = useRef(false);
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<SettingsFormValues>({
    resolver: zodResolver(settingsFormSchema),
    defaultValues,
    shouldFocusError: true,
  });
  const days = useWatch({ control, name: 'days' });

  useEffect(() => {
    reset(defaultValues);
  }, [defaultValues, reset]);

  const submit = handleSubmit(async (values) => {
    setError(null);
    setSaved(false);
    try {
      await onSubmit(values);
      setSaved(true);
    } catch (caught) {
      if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        let first: FieldName | null = null;
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

  const message = (key: string | undefined) =>
    key ? t(key as SettingsMessageKey) : undefined;
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  /** A labelled text field with its error. */
  function textField(
    name: 'centerName' | 'phone' | 'email' | 'address',
    label: string,
    options: { type?: string; maxLength: number; autoComplete?: string },
  ) {
    const fieldId = `${id}-${name}`;
    const errorId = `${fieldId}-error`;
    const fieldMessage = message(errors[name]?.message);
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId} className="text-sm font-medium">
          {label}
        </label>
        <Input
          id={fieldId}
          type={options.type ?? 'text'}
          autoComplete={options.autoComplete ?? 'off'}
          maxLength={options.maxLength}
          aria-invalid={fieldMessage ? true : undefined}
          aria-describedby={fieldMessage ? errorId : undefined}
          {...register(name)}
        />
        <FieldError id={errorId} message={fieldMessage} />
      </div>
    );
  }

  const hoursHelpId = `${id}-hours-help`;
  const zoneId = `${id}-zone`;
  const zoneHelpId = `${zoneId}-help`;

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={
            isConflict
              ? () => {
                  setError(null);
                  void onReload();
                }
              : undefined
          }
          retryLabel={isConflict ? t('settings.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-8"
      >
        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-2 text-lg font-semibold">
            {t('settings.brand.title')}
          </legend>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {textField('centerName', t('settings.brand.centerName'), {
              maxLength: CENTER_NAME_MAX_LENGTH,
              autoComplete: 'organization',
            })}
            {textField('phone', t('settings.brand.phone'), {
              type: 'tel',
              maxLength: PHONE_MAX_LENGTH,
            })}
            {textField('email', t('settings.brand.email'), {
              type: 'email',
              maxLength: EMAIL_MAX_LENGTH,
            })}
            {textField('address', t('settings.brand.address'), {
              maxLength: ADDRESS_MAX_LENGTH,
            })}
          </div>
        </fieldset>

        <fieldset
          className="flex min-w-0 flex-col gap-4"
          aria-describedby={hoursHelpId}
        >
          <legend className="mb-2 text-lg font-semibold">
            {t('settings.hours.title')}
          </legend>
          <p id={hoursHelpId} className="text-sm text-muted-foreground">
            {t('settings.hours.help')}
          </p>
          <ul className="flex flex-col gap-4">
            {WEEKDAYS.map((weekday, index) => {
              const day = t(WEEKDAY_KEYS[weekday]);
              const open = days[index]?.open ?? false;
              const rowId = `${id}-day-${weekday}`;
              const opensMessage = message(
                errors.days?.[index]?.opensAt?.message,
              );
              const closesMessage = message(
                errors.days?.[index]?.closesAt?.message,
              );
              const opensErrorId = `${rowId}-opens-error`;
              const closesErrorId = `${rowId}-closes-error`;
              return (
                <li
                  key={weekday}
                  className="grid grid-cols-1 items-start gap-3 rounded-md border border-border p-3 sm:grid-cols-[8rem_6rem_1fr_1fr]"
                >
                  <span className="pt-2 font-medium">{day}</span>
                  <div className="flex items-center gap-2 pt-2">
                    <Controller
                      control={control}
                      name={`days.${index}.open`}
                      render={({ field }) => (
                        <Checkbox
                          id={`${rowId}-open`}
                          name={field.name}
                          ref={field.ref}
                          checked={field.value}
                          aria-label={t('settings.hours.openOn', { day })}
                          onCheckedChange={(checked) =>
                            field.onChange(checked === true)
                          }
                          onBlur={field.onBlur}
                        />
                      )}
                    />
                    <label htmlFor={`${rowId}-open`} className="text-sm">
                      {t('settings.hours.open')}
                    </label>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor={`${rowId}-opens`}
                      className="text-sm font-medium"
                    >
                      {t('settings.hours.opensAt')}
                    </label>
                    <Input
                      id={`${rowId}-opens`}
                      type="time"
                      step={TIME_STEP_SECONDS}
                      disabled={!open}
                      aria-label={t('settings.hours.dayOpensAt', { day })}
                      aria-invalid={opensMessage ? true : undefined}
                      aria-describedby={opensMessage ? opensErrorId : undefined}
                      {...register(`days.${index}.opensAt`)}
                    />
                    <FieldError id={opensErrorId} message={opensMessage} />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor={`${rowId}-closes`}
                      className="text-sm font-medium"
                    >
                      {t('settings.hours.closesAt')}
                    </label>
                    <Input
                      id={`${rowId}-closes`}
                      type="time"
                      step={TIME_STEP_SECONDS}
                      disabled={!open}
                      aria-label={t('settings.hours.dayClosesAt', { day })}
                      aria-invalid={closesMessage ? true : undefined}
                      aria-describedby={
                        closesMessage ? closesErrorId : undefined
                      }
                      {...register(`days.${index}.closesAt`)}
                    />
                    <FieldError id={closesErrorId} message={closesMessage} />
                  </div>
                </li>
              );
            })}
          </ul>
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-4">
          <legend className="mb-2 text-lg font-semibold">
            {t('settings.timeZone.title')}
          </legend>
          <div className="flex max-w-md flex-col gap-2">
            <label htmlFor={zoneId} className="text-sm font-medium">
              {t('settings.timeZone.label')}
            </label>
            <select
              id={zoneId}
              className={SELECT_CLASS}
              aria-describedby={zoneHelpId}
              {...register('timeZone')}
            >
              {zoneOptions(defaultValues.timeZone).map((zone) => (
                <option key={zone} value={zone}>
                  {zone.replaceAll('_', ' ')}
                </option>
              ))}
            </select>
            <p id={zoneHelpId} className="text-sm text-muted-foreground">
              {t('settings.timeZone.help')}
            </p>
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('settings.saving')} showLabel />
            ) : (
              t('settings.save')
            )}
          </Button>
          <p role="status" className="text-sm">
            {saved ? t('settings.saved') : ''}
          </p>
        </div>
      </form>
    </div>
  );
}
