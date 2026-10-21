import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type {
  AvailabilitySettingsUpdate,
  CatalogOption,
  TutorAvailability,
} from './api';
import {
  PREFERRED_CLASSROOMS_MAX,
  serverSettingsErrors,
  settingsBody,
  settingsValues,
  validateSettings,
  type LimitName,
  type LimitValue,
  type SettingsErrors,
  type SettingsField,
  type SettingsFormValues,
} from './availabilitySettingsRules';

// The teaching limits and preferred classrooms of the Availability section
// (#197 C1). For the tutor themselves and holders of tutor.manage, one form:
// "Teaching limits" (daily and weekly maximum in hours and minutes, the
// required break in minutes, each with "No limit"/"No break"; a help text
// says limits only produce warnings when scheduling) and "Preferred
// classrooms" (a checkbox group of the active rooms, plus stored rooms that
// were deactivated since, marked "(inactive)"). Saved with the loaded
// `version`, sending only what changed; version_conflict offers "Reload".
// Other viewers get the values as text.

export type ClassroomOptionsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly CatalogOption[] };

/** Minutes as "4 h 30 min" / "4 h" / "45 min". */
function useDuration() {
  const { t } = useTranslation();
  return (minutes: number) => {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    if (hours === 0) {
      return t('tutors.availability.settings.minutes', { count: rest });
    }
    if (rest === 0) {
      return t('tutors.availability.settings.hours', { count: hours });
    }
    return t('tutors.availability.settings.hoursMinutes', {
      hours,
      minutes: rest,
    });
  };
}

/** The read-only limits and preferred classrooms. */
export function AvailabilitySettingsList({
  availability,
}: {
  availability: TutorAvailability;
}) {
  const { t } = useTranslation();
  const id = useId();
  const duration = useDuration();
  const { limits } = availability;
  const noLimit = t('tutors.availability.settings.noLimit');
  const rows = [
    {
      label: t('tutors.availability.settings.daily'),
      value:
        limits.daily_limit_minutes === null
          ? noLimit
          : duration(limits.daily_limit_minutes),
    },
    {
      label: t('tutors.availability.settings.weekly'),
      value:
        limits.weekly_limit_minutes === null
          ? noLimit
          : duration(limits.weekly_limit_minutes),
    },
    {
      label: t('tutors.availability.settings.break'),
      value:
        limits.required_break_minutes === null
          ? t('tutors.availability.settings.noBreak')
          : t('tutors.availability.settings.minutes', {
              count: limits.required_break_minutes,
            }),
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <h3 id={`${id}-limits`} className="text-base font-semibold">
          {t('tutors.availability.settings.limitsTitle')}
        </h3>
        <p className="text-sm text-muted-foreground">
          {t('tutors.availability.settings.limitsHelp')}
        </p>
        <dl
          aria-labelledby={`${id}-limits`}
          className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm"
        >
          {rows.map((row) => (
            <div key={row.label} className="contents">
              <dt className="font-medium">{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="flex flex-col gap-2">
        <h3 id={`${id}-rooms`} className="text-base font-semibold">
          {t('tutors.availability.settings.roomsTitle')}
        </h3>
        {availability.preferred_classrooms.length === 0 ? (
          <p className="text-sm">
            {t('tutors.availability.settings.roomsEmpty')}
          </p>
        ) : (
          <ul
            aria-labelledby={`${id}-rooms`}
            className="list-disc pl-5 text-sm"
          >
            {availability.preferred_classrooms.map((room) => (
              <li key={room.id} className="break-words">
                {room.is_active
                  ? room.name
                  : t('tutors.availability.settings.inactiveRoom', {
                      name: room.name,
                    })}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface AvailabilitySettingsFormProps {
  availability: TutorAvailability;
  classrooms: ClassroomOptionsState;
  /** Sends the change; rejects with the error to show. */
  onSave: (body: AvailabilitySettingsUpdate) => Promise<void>;
  /** Refetches the availability after a version conflict. */
  onReload: () => Promise<void>;
}

const FIELD_ORDER: SettingsField[] = ['daily', 'weekly', 'break', 'rooms'];

/** The editable limits and preferred classrooms; a new `version` (saved
 * or reloaded) replaces the values. */
export function AvailabilitySettingsForm({
  availability,
  classrooms,
  onSave,
  onReload,
}: AvailabilitySettingsFormProps) {
  const { t } = useTranslation();
  const id = useId();
  const [values, setValues] = useState<SettingsFormValues>(() =>
    settingsValues(availability),
  );
  const [errors, setErrors] = useState<SettingsErrors>({});
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState('');
  const sending = useRef(false);
  const firstInput: Record<SettingsField, string> = {
    daily: `${id}-daily-hours`,
    weekly: `${id}-weekly-hours`,
    break: `${id}-break`,
    rooms: `${id}-rooms`,
  };

  // A new version from the server: the form shows it (adjusted while
  // rendering, so focus stays on the Save button).
  const [shownVersion, setShownVersion] = useState(availability.version);
  if (availability.version !== shownVersion) {
    setShownVersion(availability.version);
    setValues(settingsValues(availability));
    setErrors({});
    setChecked(false);
  }

  function update(change: Partial<SettingsFormValues>) {
    const next = { ...values, ...change };
    setValues(next);
    setStatus('');
    if (checked) {
      setErrors(validateSettings(next));
    }
  }

  function focusFirst(found: SettingsErrors) {
    const field = FIELD_ORDER.find((name) => found[name]);
    if (field === 'rooms') {
      document
        .getElementById(firstInput.rooms)
        ?.querySelector<HTMLElement>('button[role="checkbox"]')
        ?.focus();
    } else if (field) {
      document.getElementById(firstInput[field])?.focus();
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    setError(null);
    setStatus('');
    const found = validateSettings(values);
    setErrors(found);
    setChecked(true);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    const body = settingsBody(values, availability);
    if (!body) {
      setStatus(t('tutors.availability.unchanged'));
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      await onSave(body);
      setStatus(t('tutors.availability.settings.saved'));
    } catch (caught) {
      const fromServer =
        caught instanceof ApiError
          ? serverSettingsErrors(caught.fieldErrors)
          : {};
      if (Object.keys(fromServer).length > 0) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setError(caught);
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const message = (field: SettingsField) =>
    errors[field] ? t(errors[field]) : undefined;

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex flex-col gap-4"
    >
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
          retryLabel={isConflict ? t('tutors.availability.reload') : undefined}
        />
      ) : null}
      <div className="flex flex-col gap-3">
        <h3 className="text-base font-semibold">
          {t('tutors.availability.settings.limitsTitle')}
        </h3>
        <p className="text-sm text-muted-foreground">
          {t('tutors.availability.settings.limitsHelp')}
        </p>
        {(['daily', 'weekly'] as const).map((name) => (
          <LimitFields
            key={name}
            idPrefix={`${id}-${name}`}
            name={name}
            value={values[name]}
            disabled={busy}
            message={message(name)}
            onChange={(next) => update({ [name]: next })}
          />
        ))}
        <BreakFields
          id={firstInput.break}
          values={values}
          disabled={busy}
          message={message('break')}
          onChange={update}
        />
      </div>
      <RoomPicker
        id={firstInput.rooms}
        availability={availability}
        classrooms={classrooms}
        chosen={values.rooms}
        disabled={busy}
        message={message('rooms')}
        onChange={(rooms) => update({ rooms })}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy}>
          {busy ? (
            <Spinner label={t('tutors.availability.saving')} showLabel />
          ) : (
            t('tutors.availability.settings.save')
          )}
        </Button>
        <p role="status" className="text-sm">
          {status}
        </p>
      </div>
    </form>
  );
}

function LimitFields({
  idPrefix,
  name,
  value,
  disabled,
  message,
  onChange,
}: {
  idPrefix: string;
  name: LimitName;
  value: LimitValue;
  disabled: boolean;
  message: string | undefined;
  onChange: (next: LimitValue) => void;
}) {
  const { t } = useTranslation();
  const errorId = `${idPrefix}-error`;
  const invalid = message ? true : undefined;
  return (
    <fieldset className="flex flex-col gap-2" data-limit={name}>
      <legend className="text-sm font-medium">
        {t(`tutors.availability.settings.${name}`)}
      </legend>
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${idPrefix}-none`}
          checked={value.none}
          disabled={disabled}
          onCheckedChange={(next) =>
            onChange({ ...value, none: next === true })
          }
        />
        <label htmlFor={`${idPrefix}-none`} className="text-sm">
          {t('tutors.availability.settings.noLimit')}
        </label>
      </div>
      {value.none ? null : (
        <div className="flex flex-wrap items-end gap-3">
          {(['hours', 'minutes'] as const).map((part) => (
            <div key={part} className="flex flex-col gap-1">
              <label htmlFor={`${idPrefix}-${part}`} className="text-sm">
                {t(`tutors.availability.settings.${part}Label`)}
              </label>
              <Input
                id={`${idPrefix}-${part}`}
                type="number"
                inputMode="numeric"
                min={0}
                max={part === 'minutes' ? 59 : name === 'daily' ? 24 : 168}
                step={1}
                className="w-24"
                value={value[part]}
                disabled={disabled}
                aria-invalid={invalid}
                aria-describedby={message ? errorId : undefined}
                onChange={(event) =>
                  onChange({ ...value, [part]: event.target.value })
                }
              />
            </div>
          ))}
        </div>
      )}
      <FieldError id={errorId} message={message} />
    </fieldset>
  );
}

function BreakFields({
  id,
  values,
  disabled,
  message,
  onChange,
}: {
  id: string;
  values: SettingsFormValues;
  disabled: boolean;
  message: string | undefined;
  onChange: (change: Partial<SettingsFormValues>) => void;
}) {
  const { t } = useTranslation();
  const errorId = `${id}-error`;
  return (
    <fieldset className="flex flex-col gap-2" data-limit="break">
      <legend className="text-sm font-medium">
        {t('tutors.availability.settings.break')}
      </legend>
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${id}-none`}
          checked={values.breakNone}
          disabled={disabled}
          onCheckedChange={(next) => onChange({ breakNone: next === true })}
        />
        <label htmlFor={`${id}-none`} className="text-sm">
          {t('tutors.availability.settings.noBreak')}
        </label>
      </div>
      {values.breakNone ? null : (
        <div className="flex flex-col gap-1">
          <label htmlFor={id} className="text-sm">
            {t('tutors.availability.settings.minutesLabel')}
          </label>
          <Input
            id={id}
            type="number"
            inputMode="numeric"
            min={0}
            max={240}
            step={1}
            className="w-24"
            value={values.breakMinutes}
            disabled={disabled}
            aria-invalid={message ? true : undefined}
            aria-describedby={message ? errorId : undefined}
            onChange={(event) => onChange({ breakMinutes: event.target.value })}
          />
        </div>
      )}
      <FieldError id={errorId} message={message} />
    </fieldset>
  );
}

function RoomPicker({
  id,
  availability,
  classrooms,
  chosen,
  disabled,
  message,
  onChange,
}: {
  id: string;
  availability: TutorAvailability;
  classrooms: ClassroomOptionsState;
  chosen: readonly string[];
  disabled: boolean;
  message: string | undefined;
  onChange: (rooms: string[]) => void;
}) {
  const { t } = useTranslation();
  const errorId = `${id}-error`;
  const helpId = `${id}-help`;
  // The active rooms, then stored ones not among them (deactivated since,
  // or beyond the first 100), by name.
  const active = classrooms.status === 'ready' ? classrooms.items : [];
  const activeIds = new Set(active.map((room) => room.id));
  const options = [
    ...active.map((room) => ({ ...room, isActive: true })),
    ...availability.preferred_classrooms
      .filter((room) => !activeIds.has(room.id))
      .map((room) => ({
        id: room.id,
        name: room.name,
        isActive: room.is_active,
      })),
  ].sort((a, b) => a.name.localeCompare(b.name));

  function toggle(roomId: string, on: boolean) {
    onChange(
      on
        ? [...chosen.filter((value) => value !== roomId), roomId]
        : chosen.filter((value) => value !== roomId),
    );
  }

  return (
    <fieldset
      id={id}
      className="flex flex-col gap-2"
      aria-describedby={message ? `${helpId} ${errorId}` : helpId}
    >
      <legend className="text-base font-semibold">
        {t('tutors.availability.settings.roomsTitle')}
      </legend>
      <p id={helpId} className="text-sm text-muted-foreground">
        {t('tutors.availability.settings.roomsHelp', {
          max: PREFERRED_CLASSROOMS_MAX,
        })}
      </p>
      {classrooms.status === 'error' ? (
        <ErrorAlert error={classrooms.error} onRetry={classrooms.onRetry} />
      ) : null}
      {classrooms.status === 'loading' ? (
        <p className="text-sm">
          {t('tutors.availability.settings.roomsLoading')}
        </p>
      ) : null}
      {classrooms.status === 'ready' && options.length === 0 ? (
        <p className="text-sm">{t('tutors.availability.settings.noRooms')}</p>
      ) : null}
      {options.length > 0 ? (
        <ul className="grid max-h-64 grid-cols-1 gap-2 overflow-y-auto sm:grid-cols-2">
          {options.map((room) => {
            const boxId = `${id}-room-${room.id}`;
            return (
              <li key={room.id} className="flex items-center gap-2">
                <Checkbox
                  id={boxId}
                  checked={chosen.includes(room.id)}
                  disabled={disabled}
                  aria-invalid={message ? true : undefined}
                  onCheckedChange={(next) => toggle(room.id, next === true)}
                />
                <label htmlFor={boxId} className="min-w-0 text-sm break-words">
                  {room.isActive
                    ? room.name
                    : t('tutors.availability.settings.inactiveRoom', {
                        name: room.name,
                      })}
                </label>
              </li>
            );
          })}
        </ul>
      ) : null}
      <FieldError id={errorId} message={message} />
    </fieldset>
  );
}
