import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import {
  addWindowId,
  newRowKey,
  WINDOW_TIME_STEP_SECONDS,
  WINDOW_WEEKDAYS,
  windowFieldId,
  WINDOWS_MAX,
  type WindowRow,
  type WindowRowErrors,
} from './windows';

// The weekly windows editor (#39 D3, D6; shared with #45 tutor
// availability): a fieldset of rows, each a weekday select, a start and an
// end time (`type="time"`, 15-minute step) and "Remove"; "Add window" below
// (disabled at 28 rows, or `max`). Controlled: the parent keeps the rows and passes
// the errors of each row (message keys, from `validateWindows` or the
// server). Every control names its row ("Window 2 start time"). Focus moves
// to the new row's weekday after "Add window" and to "Add window" after a
// removal.

export interface WeeklyWindowsEditorProps {
  /** A page-unique prefix of the controls' ids (see `windowFieldId`). */
  id: string;
  rows: readonly WindowRow[];
  onChange: (rows: WindowRow[]) => void;
  /** The errors of each row, by index. */
  errors?: readonly WindowRowErrors[];
  disabled?: boolean;
  /** The fieldset's legend; "Weekly availability" by default. */
  legend?: string;
  /** The help under the legend; the default explains "no windows". */
  help?: string;
  /** The text without rows; the default says "available at any time". */
  emptyText?: string;
  /** The most rows (the API's limit); 28 (rooms) by default. */
  max?: number;
}

export function WeeklyWindowsEditor({
  id,
  rows,
  onChange,
  errors = [],
  disabled = false,
  legend,
  help,
  emptyText,
  max = WINDOWS_MAX,
}: WeeklyWindowsEditorProps) {
  const { t } = useTranslation();
  const focusNext = useRef<string | null>(null);
  const helpId = `${id}-help`;
  const limitId = `${id}-limit`;
  const full = rows.length >= max;

  // After an add or a remove, move focus once the rows are rendered.
  useEffect(() => {
    const target = focusNext.current;
    if (target === null) {
      return;
    }
    focusNext.current = null;
    document.getElementById(target)?.focus();
  }, [rows]);

  function update(index: number, change: Partial<WindowRow>) {
    onChange(
      rows.map((row, at) => (at === index ? { ...row, ...change } : row)),
    );
  }

  function add() {
    const last = rows.at(-1);
    const row: WindowRow = {
      key: newRowKey(),
      weekday: last?.weekday ?? 1,
      startsAt: '',
      endsAt: '',
    };
    focusNext.current = windowFieldId(id, row.key, 'weekday');
    onChange([...rows, row]);
  }

  function remove(index: number) {
    focusNext.current = addWindowId(id);
    onChange(rows.filter((_, at) => at !== index));
  }

  const message = (key: string | undefined) =>
    key ? t(key as NonNullable<WindowRowErrors['startsAt']>) : undefined;

  return (
    <fieldset className="flex flex-col gap-3" aria-describedby={helpId}>
      <legend className="mb-1 text-sm font-medium">
        {legend ?? t('weeklyWindows.legend')}
      </legend>
      <p id={helpId} className="text-sm text-muted-foreground">
        {help ?? t('weeklyWindows.help')}
      </p>
      {rows.length === 0 ? (
        <p className="text-sm">{emptyText ?? t('weeklyWindows.empty')}</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {rows.map((row, index) => {
            const number = index + 1;
            const rowErrors = errors[index] ?? {};
            const ids = {
              weekday: windowFieldId(id, row.key, 'weekday'),
              startsAt: windowFieldId(id, row.key, 'startsAt'),
              endsAt: windowFieldId(id, row.key, 'endsAt'),
            };
            const messages = {
              weekday: message(rowErrors.weekday),
              startsAt: message(rowErrors.startsAt),
              endsAt: message(rowErrors.endsAt),
            };
            const described = (field: keyof typeof ids) =>
              messages[field] ? `${ids[field]}-error` : undefined;
            return (
              <li
                key={row.key}
                className="grid grid-cols-1 items-start gap-2 rounded-md border border-border p-3 sm:grid-cols-[1fr_7rem_7rem_auto]"
              >
                <div className="flex flex-col gap-1">
                  <label htmlFor={ids.weekday} className="text-sm font-medium">
                    {t('weeklyWindows.weekday')}
                  </label>
                  <select
                    id={ids.weekday}
                    className={SELECT_CLASS}
                    value={row.weekday}
                    disabled={disabled}
                    aria-label={t('weeklyWindows.weekdayLabel', { number })}
                    aria-invalid={messages.weekday ? true : undefined}
                    aria-describedby={described('weekday')}
                    onChange={(event) =>
                      update(index, { weekday: Number(event.target.value) })
                    }
                  >
                    {WINDOW_WEEKDAYS.map((weekday) => (
                      <option key={weekday} value={weekday}>
                        {t(`weeklyWindows.weekdays.${weekday}`)}
                      </option>
                    ))}
                  </select>
                  <FieldError
                    id={`${ids.weekday}-error`}
                    message={messages.weekday}
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label htmlFor={ids.startsAt} className="text-sm font-medium">
                    {t('weeklyWindows.startsAt')}
                  </label>
                  <Input
                    id={ids.startsAt}
                    type="time"
                    step={WINDOW_TIME_STEP_SECONDS}
                    value={row.startsAt}
                    disabled={disabled}
                    aria-label={t('weeklyWindows.startsAtLabel', { number })}
                    aria-invalid={messages.startsAt ? true : undefined}
                    aria-describedby={described('startsAt')}
                    onChange={(event) =>
                      update(index, { startsAt: event.target.value })
                    }
                  />
                  <FieldError
                    id={`${ids.startsAt}-error`}
                    message={messages.startsAt}
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label htmlFor={ids.endsAt} className="text-sm font-medium">
                    {t('weeklyWindows.endsAt')}
                  </label>
                  <Input
                    id={ids.endsAt}
                    type="time"
                    step={WINDOW_TIME_STEP_SECONDS}
                    value={row.endsAt}
                    disabled={disabled}
                    aria-label={t('weeklyWindows.endsAtLabel', { number })}
                    aria-invalid={messages.endsAt ? true : undefined}
                    aria-describedby={described('endsAt')}
                    onChange={(event) =>
                      update(index, { endsAt: event.target.value })
                    }
                  />
                  <FieldError
                    id={`${ids.endsAt}-error`}
                    message={messages.endsAt}
                  />
                </div>
                <div className="flex sm:pt-6">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={disabled}
                    aria-label={t('weeklyWindows.removeNamed', { number })}
                    onClick={() => remove(index)}
                  >
                    {t('weeklyWindows.remove')}
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <div className="flex flex-col items-start gap-1">
        <Button
          id={addWindowId(id)}
          type="button"
          variant="outline"
          disabled={disabled || full}
          aria-describedby={full ? limitId : undefined}
          onClick={add}
        >
          {t('weeklyWindows.add')}
        </Button>
        {full ? (
          <p id={limitId} className="text-sm text-muted-foreground">
            {t('weeklyWindows.limit', { max })}
          </p>
        ) : null}
      </div>
    </fieldset>
  );
}
