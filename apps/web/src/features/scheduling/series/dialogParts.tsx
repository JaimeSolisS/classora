import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { TimeField } from '@/components/ui/time-field';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import type { Option } from '../create/api';
import type { SeriesEditMode } from './api';
import { isVersionConflict } from './rules';

// Parts shared by the series dialogs (#54b C1-C3): the failure under the
// form (a version conflict says "This changed since you opened it" with a
// reload; anything else is the shared ErrorAlert, whose `errors` catalog
// translates session_not_editable, series_ended, …), the scope radio, the
// classroom select and the translation of a field message.

export function DialogFailure({
  error,
  onReload,
}: {
  error: unknown;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  if (isVersionConflict(error)) {
    return (
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-md border border-destructive p-3"
      >
        <p className="font-medium">{t('scheduling.series.versionConflict')}</p>
        <div>
          <Button type="button" variant="outline" onClick={onReload}>
            {t('scheduling.series.reload')}
          </Button>
        </div>
      </div>
    );
  }
  return <ErrorAlert error={error} />;
}

export interface ScopeFieldsProps {
  idPrefix: string;
  mode: SeriesEditMode;
  fromDate: string;
  error?: string;
  disabled?: boolean;
  onChange: (mode: SeriesEditMode, fromDate: string) => void;
}

/** "This and following, from <date>" or "All future occurrences" (C2). */
export function ScopeFields({
  idPrefix,
  mode,
  fromDate,
  error,
  disabled,
  onChange,
}: ScopeFieldsProps) {
  const { t } = useTranslation();
  const dateId = `${idPrefix}-from`;
  const errorId = `${idPrefix}-from-error`;
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="mb-2 text-sm font-medium">
        {t('scheduling.series.scope.legend')}
      </legend>
      <label className="flex min-h-9 items-center gap-2 text-sm">
        <input
          type="radio"
          name={`${idPrefix}-mode`}
          value="following"
          checked={mode === 'following'}
          disabled={disabled}
          onChange={() => onChange('following', fromDate)}
          className="size-4 accent-foreground"
        />
        {t('scheduling.series.scope.following')}
      </label>
      {mode === 'following' ? (
        <div className="flex flex-col gap-2 pl-6 sm:max-w-xs">
          <label htmlFor={dateId} className="text-sm font-medium">
            {t('scheduling.series.scope.fromDate')}
          </label>
          <Input
            id={dateId}
            type="date"
            value={fromDate}
            disabled={disabled}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => onChange('following', event.target.value)}
          />
          <FieldError id={errorId} message={error} />
        </div>
      ) : null}
      <label className="flex min-h-9 items-center gap-2 text-sm">
        <input
          type="radio"
          name={`${idPrefix}-mode`}
          value="all"
          checked={mode === 'all'}
          disabled={disabled}
          onChange={() => onChange('all', fromDate)}
          className="size-4 accent-foreground"
        />
        {t('scheduling.series.scope.all')}
      </label>
    </fieldset>
  );
}

export function ClassroomSelect({
  id,
  value,
  classrooms,
  current,
  error,
  disabled,
  onChange,
}: {
  id: string;
  value: string;
  classrooms: readonly Option[];
  /** The room held now, listed even when it isn't active any more. */
  current?: Option | null;
  error?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const options =
    current && !classrooms.some((room) => room.id === current.id)
      ? [current, ...classrooms]
      : classrooms;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {t('scheduling.fields.classroom')}
      </label>
      <select
        id={id}
        className={SELECT_CLASS}
        value={value}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{t('scheduling.noClassroom')}</option>
        {options.map((room) => (
          <option key={room.id} value={room.id}>
            {room.name}
          </option>
        ))}
      </select>
      <FieldError id={`${id}-error`} message={error} />
    </div>
  );
}

/**
 * A labelled date or time input with its error. A time is the type-or-pick
 * `TimeField` (#600, as "Schedule a session" in #598): `onChange` also says
 * whether its text isn't a time, `onEditEnd` ends an edit of the start and
 * `scrollTo` is where its list opens with no time.
 */
export function SlotInput({
  id,
  label,
  kind,
  value,
  error,
  describedBy,
  disabled,
  onChange,
  onEditEnd,
  scrollTo,
}: {
  id: string;
  label: string;
  /** A native date input, or the type-or-pick time field. */
  kind: 'date' | 'time';
  value: string;
  error?: string | undefined;
  describedBy?: string;
  disabled?: boolean;
  onChange: (value: string, unreadable: boolean) => void;
  onEditEnd?: (() => void) | undefined;
  scrollTo?: string | undefined;
}) {
  const ids =
    [describedBy, error ? `${id}-error` : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const labelId = `${id}-label`;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label id={labelId} htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {kind === 'time' ? (
        <TimeField
          id={id}
          labelId={labelId}
          value={value}
          invalid={Boolean(error)}
          disabled={disabled}
          aria-describedby={ids}
          scrollTo={scrollTo}
          onChange={onChange}
          onEditEnd={onEditEnd}
        />
      ) : (
        <Input
          id={id}
          type="date"
          value={value}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={ids}
          onChange={(event) => onChange(event.target.value, false)}
        />
      )}
      <FieldError id={`${id}-error`} message={error} />
    </div>
  );
}
