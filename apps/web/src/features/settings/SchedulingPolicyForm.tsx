import { zodResolver } from '@hookform/resolvers/zod';
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { addDays, type LocalDate } from '@/lib/time';
import type { SchedulingPolicy } from './api';
import {
  NUMBER_RANGES,
  policyFormSchema,
  policyValues,
  rulesChanged,
  SERVER_FIELDS,
  type FlagField,
  type NumberField,
  type PolicyFormValues,
  type PolicyMessageKey,
} from './policyRules';

// "Change policy" (#61 F2): React Hook Form + Zod mirroring
// SchedulingPolicyCreate, prefilled from the latest version (the last
// upcoming one, else the one in force). Number inputs for the hours and
// days with their ranges, three makeup checkboxes and "Takes effect":
// "Immediately" or "From date" (a date after today in the center's zone;
// the page sends the start of that day there). Save stays disabled until a
// rule differs from the latest version. Field errors land on their fields;
// version_conflict ("Reload"), invalid_effective_from and other errors show
// in the alert region. No API call of its own: `onSubmit` rejects with the
// error to show; a new `latest` (after a save or a reload) resets the form.

interface SchedulingPolicyFormProps {
  /** The version the form starts from and that `based_on_id` names. */
  latest: SchedulingPolicy;
  /** Today's date in the center's zone. */
  today: LocalDate;
  /** The center's zone, named in the date's help text. */
  timeZone: string;
  onSubmit: (values: PolicyFormValues) => Promise<void>;
  /** Refetches the policy after a version conflict. */
  onReload: () => Promise<void>;
  /** The id of the heading that names the form. */
  labelledBy: string;
}

const NUMBER_FIELDS: { name: NumberField; help?: string }[] = [
  {
    name: 'changeNoticeHours',
    help: 'settings.schedulingPolicies.fields.changeNoticeHelp',
  },
  {
    name: 'bookingNoticeHours',
    help: 'settings.schedulingPolicies.fields.bookingNoticeHelp',
  },
  { name: 'responseHours' },
  { name: 'makeupWindowDays' },
];
const FLAG_FIELDS: FlagField[] = [
  'makeupAfterOnTime',
  'makeupAfterLate',
  'makeupAfterNoShow',
];

export function SchedulingPolicyForm({
  latest,
  today,
  timeZone,
  onSubmit,
  onReload,
  labelledBy,
}: SchedulingPolicyFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const defaultValues = useMemo(() => policyValues(latest), [latest]);
  const schema = useMemo(() => policyFormSchema(today), [today]);
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    formState: { errors, isSubmitting },
  } = useForm<PolicyFormValues>({
    resolver: zodResolver(schema),
    defaultValues,
    shouldFocusError: true,
  });
  const values = useWatch({ control }) as PolicyFormValues;
  const changed = rulesChanged({ ...defaultValues, ...values }, latest);
  const takesEffect = values.takesEffect ?? 'now';

  useEffect(() => {
    reset(defaultValues);
  }, [defaultValues, reset]);

  const submit = handleSubmit(async (submitted) => {
    setError(null);
    try {
      await onSubmit(submitted);
    } catch (caught) {
      if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        let first: keyof PolicyFormValues | null = null;
        for (const fieldError of caught.fieldErrors) {
          const mapped = SERVER_FIELDS[String(fieldError.path[1])];
          if (mapped) {
            setFieldError(mapped, { message: fieldError.message });
            first ??= mapped;
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

  /** A catalog message, or a server message as it came. */
  const message = (key: string | undefined) =>
    key?.startsWith('settings.') ? t(key as PolicyMessageKey) : key;
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  const dateId = `${id}-date`;
  const dateHelpId = `${dateId}-help`;
  const dateErrorId = `${dateId}-error`;
  const dateMessage = message(errors.effectiveDate?.message);

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
          retryLabel={
            isConflict ? t('settings.schedulingPolicies.reload') : undefined
          }
        />
      ) : null}
      <form
        method="post"
        noValidate
        aria-labelledby={labelledBy}
        onSubmit={onFormSubmit}
        className="flex flex-col gap-6"
      >
        <p className="text-sm text-muted-foreground">
          {t('settings.schedulingPolicies.form.help')}
        </p>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {NUMBER_FIELDS.map(({ name, help }) => {
            const fieldId = `${id}-${name}`;
            const errorId = `${fieldId}-error`;
            const helpId = `${fieldId}-help`;
            const fieldMessage = message(errors[name]?.message);
            const [min, max] = NUMBER_RANGES[name];
            const describedBy = [
              help ? helpId : null,
              fieldMessage ? errorId : null,
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <div key={name} className="flex flex-col gap-2">
                <label htmlFor={fieldId} className="text-sm font-medium">
                  {t(`settings.schedulingPolicies.fields.${name}`)}
                </label>
                <Input
                  id={fieldId}
                  type="number"
                  inputMode="numeric"
                  min={min}
                  max={max}
                  step={1}
                  aria-invalid={fieldMessage ? true : undefined}
                  aria-describedby={describedBy || undefined}
                  {...register(name)}
                />
                {help ? (
                  <p id={helpId} className="text-sm text-muted-foreground">
                    {t(help as PolicyMessageKey)}
                  </p>
                ) : null}
                <FieldError id={errorId} message={fieldMessage} />
              </div>
            );
          })}
        </div>

        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="mb-2 text-sm font-medium">
            {t('settings.schedulingPolicies.form.makeups')}
          </legend>
          {FLAG_FIELDS.map((name) => {
            const fieldId = `${id}-${name}`;
            return (
              <div key={name} className="flex items-center gap-2">
                <Controller
                  control={control}
                  name={name}
                  render={({ field }) => (
                    <Checkbox
                      id={fieldId}
                      name={field.name}
                      ref={field.ref}
                      checked={field.value}
                      onCheckedChange={(checked) =>
                        field.onChange(checked === true)
                      }
                      onBlur={field.onBlur}
                    />
                  )}
                />
                <label htmlFor={fieldId} className="text-sm">
                  {t(`settings.schedulingPolicies.fields.${name}`)}
                </label>
              </div>
            );
          })}
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="mb-2 text-sm font-medium">
            {t('settings.schedulingPolicies.takesEffect.legend')}
          </legend>
          {(['now', 'date'] as const).map((option) => (
            <label
              key={option}
              htmlFor={`${id}-takes-${option}`}
              className="flex min-h-6 items-center gap-2 text-sm"
            >
              <input
                id={`${id}-takes-${option}`}
                type="radio"
                value={option}
                className="size-4 accent-primary outline-none focus-visible:ring-3 focus-visible:ring-ring"
                {...register('takesEffect')}
              />
              {t(`settings.schedulingPolicies.takesEffect.${option}`)}
            </label>
          ))}
          {takesEffect === 'date' ? (
            <div className="flex max-w-xs flex-col gap-2">
              <label htmlFor={dateId} className="text-sm font-medium">
                {t('settings.schedulingPolicies.takesEffect.dateLabel')}
              </label>
              <Input
                id={dateId}
                type="date"
                min={addDays(today, 1)}
                aria-invalid={dateMessage ? true : undefined}
                aria-describedby={
                  dateMessage ? `${dateHelpId} ${dateErrorId}` : dateHelpId
                }
                {...register('effectiveDate')}
              />
              <p id={dateHelpId} className="text-sm text-muted-foreground">
                {t('settings.schedulingPolicies.takesEffect.dateHelp', {
                  zone: timeZone.replaceAll('_', ' '),
                })}
              </p>
              <FieldError id={dateErrorId} message={dateMessage} />
            </div>
          ) : null}
        </fieldset>

        <div>
          <Button type="submit" disabled={!changed || isSubmitting}>
            {isSubmitting ? (
              <Spinner
                label={t('settings.schedulingPolicies.form.saving')}
                showLabel
              />
            ) : (
              t('settings.schedulingPolicies.form.save')
            )}
          </Button>
        </div>
      </form>
    </div>
  );
}
