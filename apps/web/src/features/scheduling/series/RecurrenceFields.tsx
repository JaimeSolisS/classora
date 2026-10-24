import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import {
  INTERVALS,
  ISO_WEEKDAYS,
  MAX_COUNT,
  type EndsKind,
  type RecurrenceErrors,
  type RecurrenceField,
  type RecurrenceMessage,
  type RecurrenceValues,
  recurrenceFieldId,
} from './recurrence';

// The "Repeats weekly" controls (#54b A1), shared by the create form and
// the "Edit series" dialog: the weekdays as checkboxes in a fieldset with a
// legend (Monday to Sunday, ISO 1-7), "Every N weeks" (1-4) and "Ends"
// (never, on a date, after N sessions; left out with `showEnds={false}`,
// #243). Errors sit under their control and
// are tied to it with aria-describedby (the group's error to the fieldset).

export interface RecurrenceFieldsProps {
  /** A unique prefix for the controls' ids (see `recurrenceFieldId`). */
  idPrefix: string;
  values: RecurrenceValues;
  errors: RecurrenceErrors;
  onChange: (values: RecurrenceValues) => void;
  disabled?: boolean;
  /** Show "Ends" (default); a cohort's rule has none (#243 C2). */
  showEnds?: boolean;
}

type Weekday = '1' | '2' | '3' | '4' | '5' | '6' | '7';

const ENDS: readonly EndsKind[] = ['never', 'until', 'count'];

export function RecurrenceFields({
  idPrefix,
  values,
  errors,
  onChange,
  disabled,
  showEnds = true,
}: RecurrenceFieldsProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const errorId = (field: RecurrenceField) => `${idPrefix}-${field}-error`;

  function message(key: RecurrenceMessage | undefined): string | undefined {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'validation_error')
      : t(key as 'scheduling.series.validation.weekdaysRequired');
  }

  function set<K extends keyof RecurrenceValues>(
    key: K,
    value: RecurrenceValues[K],
  ) {
    onChange({ ...values, [key]: value });
  }

  function toggleWeekday(day: number, checked: boolean) {
    set(
      'weekdays',
      checked
        ? [...values.weekdays.filter((d) => d !== day), day].sort(
            (a, b) => a - b,
          )
        : values.weekdays.filter((d) => d !== day),
    );
  }

  const invalid = (field: RecurrenceField) =>
    errors[field] ? true : undefined;
  const describedBy = (field: RecurrenceField) =>
    errors[field] ? errorId(field) : undefined;

  return (
    <div className="flex flex-col gap-4">
      <fieldset
        className="flex min-w-0 flex-col gap-2"
        aria-describedby={describedBy('weekdays')}
      >
        <legend className="mb-2 text-sm font-medium">
          {t('scheduling.series.weekdays')}
        </legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {ISO_WEEKDAYS.map((day) => (
            <label
              key={day}
              className="flex min-h-9 items-center gap-2 text-sm"
            >
              <input
                id={`${idPrefix}-weekday-${day}`}
                type="checkbox"
                value={day}
                checked={values.weekdays.includes(day)}
                disabled={disabled}
                onChange={(event) => toggleWeekday(day, event.target.checked)}
                className="size-4 accent-foreground"
              />
              {t(`weeklyWindows.weekdays.${String(day) as Weekday}`)}
            </label>
          ))}
        </div>
        <FieldError
          id={errorId('weekdays')}
          message={message(errors.weekdays)}
        />
      </fieldset>
      <div className="flex flex-col gap-2 sm:max-w-xs">
        <label
          htmlFor={recurrenceFieldId(idPrefix, 'interval')}
          className="text-sm font-medium"
        >
          {t('scheduling.series.interval')}
        </label>
        <select
          id={recurrenceFieldId(idPrefix, 'interval')}
          className={SELECT_CLASS}
          value={values.interval}
          disabled={disabled}
          aria-invalid={invalid('interval')}
          aria-describedby={describedBy('interval')}
          onChange={(event) => set('interval', Number(event.target.value))}
        >
          {INTERVALS.map((n) => (
            <option key={n} value={n}>
              {t('scheduling.series.intervalOption', { count: n })}
            </option>
          ))}
        </select>
        <FieldError
          id={errorId('interval')}
          message={message(errors.interval)}
        />
      </div>
      {showEnds ? (
        <fieldset
          className="flex min-w-0 flex-col gap-2"
          aria-describedby={describedBy('ends')}
        >
          <legend className="mb-2 text-sm font-medium">
            {t('scheduling.series.ends')}
          </legend>
          {ENDS.map((kind) => (
            <div key={kind} className="flex flex-col gap-2">
              <label className="flex min-h-9 items-center gap-2 text-sm">
                <input
                  id={`${idPrefix}-ends-${kind}`}
                  type="radio"
                  name={`${idPrefix}-ends`}
                  value={kind}
                  checked={values.ends === kind}
                  disabled={disabled}
                  onChange={() => set('ends', kind)}
                  className="size-4 accent-foreground"
                />
                {t(`scheduling.series.endsOptions.${kind}`)}
              </label>
              {kind === 'until' && values.ends === 'until' ? (
                <div className="flex flex-col gap-2 pl-6 sm:max-w-xs">
                  <label
                    htmlFor={recurrenceFieldId(idPrefix, 'until')}
                    className="text-sm font-medium"
                  >
                    {t('scheduling.series.untilDate')}
                  </label>
                  <Input
                    id={recurrenceFieldId(idPrefix, 'until')}
                    type="date"
                    value={values.until}
                    disabled={disabled}
                    aria-invalid={invalid('until')}
                    aria-describedby={describedBy('until')}
                    onChange={(event) => set('until', event.target.value)}
                  />
                  <FieldError
                    id={errorId('until')}
                    message={message(errors.until)}
                  />
                </div>
              ) : null}
              {kind === 'count' && values.ends === 'count' ? (
                <div className="flex flex-col gap-2 pl-6 sm:max-w-xs">
                  <label
                    htmlFor={recurrenceFieldId(idPrefix, 'count')}
                    className="text-sm font-medium"
                  >
                    {t('scheduling.series.count')}
                  </label>
                  <Input
                    id={recurrenceFieldId(idPrefix, 'count')}
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={MAX_COUNT}
                    step={1}
                    value={values.count}
                    disabled={disabled}
                    aria-invalid={invalid('count')}
                    aria-describedby={[
                      `${idPrefix}-count-help`,
                      describedBy('count'),
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    onChange={(event) => set('count', event.target.value)}
                  />
                  <p
                    id={`${idPrefix}-count-help`}
                    className="text-sm text-muted-foreground"
                  >
                    {t('scheduling.series.countHelp', { max: MAX_COUNT })}
                  </p>
                  <FieldError
                    id={errorId('count')}
                    message={message(errors.count)}
                  />
                </div>
              ) : null}
            </div>
          ))}
          <FieldError id={errorId('ends')} message={message(errors.ends)} />
        </fieldset>
      ) : null}
    </div>
  );
}
