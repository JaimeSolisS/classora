import { useQuery } from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { PICKER_LIMIT } from './api';
import { SEARCH_MAX_LENGTH } from './rules';

// A labelled search field with its results as a native radio group (#34 I2,
// I4, J2): the account picker of the guardian form and the guardian picker
// of the student's link dialog. No new dependency. Typing waits 300 ms, then
// up to 10 results appear; a polite status announces how many. Tab reaches
// the group and the arrow keys move the selection (native radios), which is
// shown checked. Disabled options (an archived account) stay visible.

/** How long typing must pause before the search runs. */
export const PICKER_DEBOUNCE_MS = 300;

interface SearchPickerProps<T> {
  /** The search field's label. */
  label: string;
  /** Extra help under the label (also described by the field). */
  help?: string;
  /** The query key prefix of this picker's searches. */
  queryKey: readonly unknown[];
  /** Runs the search (at most PICKER_LIMIT results). */
  search: (q: string, signal: AbortSignal) => Promise<T[]>;
  optionId: (item: T) => string;
  optionLabel: (item: T) => string;
  optionDisabled?: (item: T) => boolean;
  /** The checked option's id. */
  selectedId: string | null;
  onSelect: (item: T) => void;
  /** An error message for the field (aria-invalid + described). */
  errorMessage?: string;
  /** Focus the search field when it mounts (a dialog's first field). */
  autoFocus?: boolean;
}

export function SearchPicker<T>({
  label,
  help,
  queryKey,
  search,
  optionId,
  optionLabel,
  optionDisabled,
  selectedId,
  onSelect,
  errorMessage,
  autoFocus,
}: SearchPickerProps<T>) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    field: `${id}-search`,
    help: `${id}-help`,
    count: `${id}-count`,
    error: `${id}-error`,
    results: `${id}-results`,
  };
  const [text, setText] = useState('');
  const [q, setQ] = useState('');

  useEffect(() => {
    const next = text.trim();
    if (next === q) {
      return;
    }
    const timer = window.setTimeout(() => setQ(next), PICKER_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [text, q]);

  const results = useQuery({
    queryKey: [...queryKey, 'picker', q],
    queryFn: ({ signal }) => search(q, signal),
    enabled: q !== '',
  });
  const items = q !== '' ? (results.data ?? []) : [];
  const shown = items.slice(0, PICKER_LIMIT);

  let count = '';
  if (q !== '' && results.isSuccess) {
    count =
      shown.length === 0
        ? t('guardians.picker.none')
        : t('guardians.picker.count', { count: shown.length });
  } else if (q !== '' && results.isError) {
    count = t('guardians.picker.failed');
  }

  const describedBy = [
    help ? ids.help : undefined,
    ids.count,
    errorMessage ? ids.error : undefined,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={ids.field} className="text-sm font-medium">
        {label}
      </label>
      {help ? (
        <p id={ids.help} className="text-sm text-muted-foreground">
          {help}
        </p>
      ) : null}
      <Input
        id={ids.field}
        type="search"
        autoComplete="off"
        spellCheck={false}
        maxLength={SEARCH_MAX_LENGTH}
        autoFocus={autoFocus}
        aria-invalid={errorMessage ? true : undefined}
        aria-describedby={describedBy}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {errorMessage ? (
        <p id={ids.error} className="text-sm text-destructive">
          {errorMessage}
        </p>
      ) : null}
      <p id={ids.count} role="status" className="text-sm">
        {count}
      </p>
      {shown.length > 0 ? (
        <fieldset className="flex flex-col gap-1">
          <legend className="sr-only">
            {t('guardians.picker.results', { label })}
          </legend>
          {shown.map((item) => {
            const value = optionId(item);
            const disabled = optionDisabled?.(item) ?? false;
            const checked = value === selectedId;
            return (
              <label
                key={value}
                className={cn(
                  'flex min-h-9 items-center gap-3 rounded-md border px-3 py-1.5 text-sm',
                  checked
                    ? 'border-ring bg-muted font-medium'
                    : 'border-border',
                  disabled
                    ? 'cursor-not-allowed text-muted-foreground'
                    : 'cursor-pointer has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring',
                )}
              >
                <input
                  type="radio"
                  name={ids.results}
                  value={value}
                  checked={checked}
                  disabled={disabled}
                  className="size-4 shrink-0 accent-primary"
                  onChange={() => onSelect(item)}
                />
                <span className="min-w-0 break-words">{optionLabel(item)}</span>
              </label>
            );
          })}
        </fieldset>
      ) : null}
    </div>
  );
}
