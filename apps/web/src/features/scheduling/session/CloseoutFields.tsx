import type { Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  MINUTES_MAX,
  MINUTES_MIN,
  SUMMARY_FIELDS,
  SUMMARY_MAX,
  isRequired,
  type SummaryDraft,
  type SummaryErrors,
  type SummaryField,
} from '@/features/tutor-portal/closeout/rules';
import { REVIEW_TEXTAREA_CLASS } from './ReviewFailure';

// The actual minutes and the four summary fields of a staff closeout
// dialog (#78 "Correct", #301b "Close out on behalf" / "Take over"): the
// minutes with the scheduled minutes as a hint, "Everyone was absent, so
// the summary is optional", and each summary field with its required or
// optional mark (#77 E4), its error and a counter. The dialog owns the
// values, the validation and which errors show; `locked` makes every field
// read-only and hides the marks (Re-approve).

const FIELD_KEYS = {
  topics: 'closeout.fields.topics',
  performance: 'closeout.fields.performance',
  homework: 'closeout.fields.homework',
  next_steps: 'closeout.fields.next_steps',
} as const;

export interface CloseoutFieldsProps {
  /** A unique prefix for the field ids (`useId()`). */
  id: string;
  minutes: string;
  onMinutes: (value: string) => void;
  /** Whether the minutes show their range error. */
  minutesInvalid: boolean;
  minutesRef?: Ref<HTMLInputElement>;
  scheduledMinutes: number;
  draft: SummaryDraft;
  onField: (field: SummaryField, value: string) => void;
  /** The summary errors to show (already filtered by the dialog). */
  errors: SummaryErrors;
  allAbsent: boolean;
  locked?: boolean;
  disabled?: boolean;
}

export function CloseoutFields({
  id,
  minutes,
  onMinutes,
  minutesInvalid,
  minutesRef,
  scheduledMinutes,
  draft,
  onField,
  errors,
  allAbsent,
  locked = false,
  disabled = false,
}: CloseoutFieldsProps) {
  const { t } = useTranslation();
  const minutesId = `${id}-minutes`;
  return (
    <>
      <div className="flex max-w-xs flex-col gap-2">
        <label htmlFor={minutesId} className="text-sm font-medium">
          {t('closeout.actualMinutes')}
        </label>
        <Input
          id={minutesId}
          ref={minutesRef}
          type="number"
          inputMode="numeric"
          min={MINUTES_MIN}
          max={MINUTES_MAX}
          step={1}
          value={minutes}
          readOnly={locked}
          disabled={disabled}
          aria-invalid={minutesInvalid ? true : undefined}
          aria-describedby={`${minutesId}-hint ${minutesId}-error`}
          onChange={(event) => onMinutes(event.target.value)}
        />
        <p id={`${minutesId}-hint`} className="text-sm text-muted-foreground">
          {t('closeout.scheduled', { minutes: scheduledMinutes })}
        </p>
        <FieldError
          id={`${minutesId}-error`}
          message={
            minutesInvalid
              ? t('closeout.minutesRange', {
                  min: MINUTES_MIN,
                  max: MINUTES_MAX,
                })
              : undefined
          }
        />
      </div>
      {allAbsent && !locked ? (
        <p className="text-sm">{t('closeout.allAbsent')}</p>
      ) : null}
      {SUMMARY_FIELDS.map((field) => {
        const fieldId = `${id}-${field}`;
        const value = draft[field];
        const code = errors[field];
        const required = !locked && isRequired(field, allAbsent);
        const length = value.trim().length;
        return (
          <div key={field} className="flex min-w-0 flex-col gap-2">
            <label htmlFor={fieldId} className="text-sm font-medium">
              {t(FIELD_KEYS[field])}{' '}
              {locked ? null : (
                <span className="font-normal text-muted-foreground">
                  {required ? t('closeout.required') : t('closeout.optional')}
                </span>
              )}
            </label>
            <textarea
              id={fieldId}
              rows={3}
              value={value}
              readOnly={locked}
              disabled={disabled}
              required={required}
              aria-required={required}
              aria-invalid={code ? true : undefined}
              aria-describedby={`${fieldId}-error ${fieldId}-counter`}
              onChange={(event) => onField(field, event.target.value)}
              className={REVIEW_TEXTAREA_CLASS}
            />
            <FieldError
              id={`${fieldId}-error`}
              message={
                code
                  ? t(`closeout.errors.${code}`, { max: SUMMARY_MAX })
                  : undefined
              }
            />
            <p
              id={`${fieldId}-counter`}
              className={
                length > SUMMARY_MAX
                  ? 'text-sm text-destructive'
                  : 'text-sm text-muted-foreground'
              }
            >
              {t('scheduling.substitution.counter', {
                length,
                max: SUMMARY_MAX,
              })}
            </p>
          </div>
        );
      })}
    </>
  );
}
