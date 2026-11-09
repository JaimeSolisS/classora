import { useQuery } from '@tanstack/react-query';
import { ChevronDownIcon } from 'lucide-react';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from '@/components/ui/popover';
import {
  matchesSearch,
  SEARCH_MAX_LENGTH,
  SEARCH_MAX_TERMS,
} from '@/lib/search';
import { cn } from '@/lib/utils';

// A type-ahead combobox (#545): the WAI-ARIA APG "combobox with list
// autocomplete" over the existing Input and the pinned radix-ui Popover (no
// new dependency, E1). Clicking the field (or typing, ArrowDown, the
// chevron) shows the first page of options; typing filters them. When the
// first page holds every option, filtering is local and instant (accent and
// case insensitive, every term in any order); otherwise the text goes to
// the server as `q` after a 300 ms pause (E2). DOM focus stays in the field:
// the active option is `aria-activedescendant`. The popup is portalled, so a
// dialog's scroll area never clips it.

/** How long typing must pause before a server search runs. */
export const PICKER_DEBOUNCE_MS = 300;

/** The options a loader requests at once (the API's maximum page). */
export const COMBOBOX_PAGE_SIZE = 100;

/** A page of options: what the loader returns. */
export interface OptionsPage<T> {
  items: T[];
  /** Every option matching the request, loaded or not. */
  total: number;
}

/** The chosen option, as the caller holds it (a deep link, a form reset). */
export interface ComboboxSelection {
  id: string;
  label: string;
}

export interface ComboboxProps<T> {
  /** The field's visible label (also the list's name). */
  label: string;
  /** Extra help under the label (also described by the field). */
  help?: string;
  /** The query key prefix of this field's requests (per user source). */
  queryKey: readonly unknown[];
  /** Loads the first page (`q` null) or the server's matches for `q`. */
  load: (q: string | null, signal: AbortSignal) => Promise<OptionsPage<T>>;
  optionId: (item: T) => string;
  optionLabel: (item: T) => string;
  /** The text local filtering matches (defaults to the label). */
  optionSearchText?: (item: T) => string;
  optionDisabled?: (item: T) => boolean;
  /** A short note after the label (e.g. "already enrolled"). */
  optionNote?: (item: T) => string | undefined;
  /** The chosen option; null when nothing is chosen. */
  selected: ComboboxSelection | null;
  onSelect: (item: T) => void;
  /**
   * `single` shows the choice in the field; `add` reports each choice,
   * clears the field and keeps the list open (a list of chips).
   */
  mode?: 'single' | 'add';
  /** Shown when the source holds no option at all. */
  emptyText?: string;
  /** An error message for the field (aria-invalid + described). */
  errorMessage?: string;
  /** Focus the field when it mounts (a dialog's first field). */
  autoFocus?: boolean;
  /** Extra classes for the field and each option (44 px targets, #444 D3). */
  inputClassName?: string;
  optionClassName?: string;
}

/** The text sent as `q`: trimmed, at most the API's 5 words. */
function serverQuery(text: string): string {
  return text.trim().split(/\s+/).slice(0, SEARCH_MAX_TERMS).join(' ');
}

/** The active option on open by ArrowDown: the first enabled one. */
const FIRST = Symbol('first');
/** The active option on open by ArrowUp: the last enabled one. */
const LAST = Symbol('last');

export function Combobox<T>({
  label,
  help,
  queryKey,
  load,
  optionId,
  optionLabel,
  optionSearchText = optionLabel,
  optionDisabled,
  optionNote,
  selected,
  onSelect,
  mode = 'single',
  emptyText,
  errorMessage,
  autoFocus,
  inputClassName,
  optionClassName,
}: ComboboxProps<T>) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    label: `${id}-label`,
    field: `${id}-field`,
    help: `${id}-help`,
    status: `${id}-status`,
    error: `${id}-error`,
    failed: `${id}-failed`,
    listbox: `${id}-listbox`,
  };
  const optionDomId = (index: number) => `${id}-option-${index}`;
  const anchorRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [text, setText] = useState('');
  // Whether the field shows typed text (true) or the selection (false).
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState(false);
  // The first page is requested on first focus or open (A4).
  const [armed, setArmed] = useState(false);
  const [active, setActive] = useState<string | typeof FIRST | typeof LAST>();
  const [serverQ, setServerQ] = useState<string | null>(null);

  const shownText = dirty
    ? text
    : mode === 'add'
      ? ''
      : (selected?.label ?? '');
  const query = dirty ? text : '';
  const wanted = serverQuery(query);

  const first = useQuery({
    queryKey: [...queryKey, 'combobox', null],
    queryFn: ({ signal }) => load(null, signal),
    enabled: armed,
  });
  const firstPage = first.data;
  const complete =
    firstPage !== undefined && firstPage.total <= firstPage.items.length;
  const serverMode = firstPage !== undefined && !complete;

  // Server mode: the trimmed text goes out after a pause; blank never does.
  useEffect(() => {
    if (!serverMode || wanted === '' || wanted === serverQ) {
      return;
    }
    const timer = window.setTimeout(
      () => setServerQ(wanted),
      PICKER_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [serverMode, wanted, serverQ]);

  const searching = serverMode && wanted !== '';
  const searched = useQuery({
    queryKey: [...queryKey, 'combobox', serverQ],
    queryFn: ({ signal }) => load(serverQ, signal),
    enabled: armed && searching && serverQ !== null && serverQ === wanted,
  });

  const localRows = (firstPage?.items ?? []).filter((item) =>
    matchesSearch(optionSearchText(item), query),
  );
  // The rows always belong to the current text: a server page only when it
  // answers exactly that text, else the loaded ones filtered locally.
  let rows: T[] = localRows;
  let page: OptionsPage<T> | undefined;
  let busy = armed && first.isPending;
  let failed = first.isError;
  if (searching) {
    const answered = serverQ === wanted ? searched.data : undefined;
    if (answered) {
      rows = answered.items;
      page = answered;
    } else {
      busy = !(serverQ === wanted && searched.isError);
    }
    failed = serverQ === wanted && searched.isError;
  } else if (firstPage && query.trim() === '') {
    page = firstPage;
  }
  const truncated = page !== undefined && page.total > page.items.length;

  const isDisabled = (item: T) => optionDisabled?.(item) ?? false;
  const enabledIds = rows.filter((item) => !isDisabled(item)).map(optionId);
  let activeId: string | undefined;
  if (active === FIRST) {
    activeId = enabledIds[0];
  } else if (active === LAST) {
    activeId = enabledIds.at(-1);
  } else if (active !== undefined && enabledIds.includes(active)) {
    activeId = active;
  }
  const activeIndex =
    activeId === undefined
      ? -1
      : rows.findIndex((item) => optionId(item) === activeId);

  // The active option scrolls into view (A7).
  useEffect(() => {
    if (!open || activeIndex < 0) {
      return;
    }
    const option = listRef.current?.querySelector<HTMLElement>(
      `[data-index="${activeIndex}"]`,
    );
    option?.scrollIntoView?.({ block: 'nearest' });
  }, [open, activeIndex]);

  let state = '';
  if (busy && rows.length === 0) {
    state = t('combobox.loading');
  } else if (!busy && !failed && firstPage?.total === 0) {
    state = emptyText ?? t('combobox.empty');
  } else if (!busy && !failed && rows.length === 0 && query.trim() !== '') {
    state = t('combobox.noResults', { text: query.trim() });
  }
  const footer =
    truncated && page
      ? t('combobox.truncated', {
          shown: page.items.length,
          total: page.total,
        })
      : '';
  let announcement = '';
  if (failed) {
    announcement = t('combobox.failed');
  } else if (open) {
    announcement =
      state ||
      [t('combobox.count', { count: rows.length }), footer]
        .filter(Boolean)
        .join(' ');
  }

  function openList(next?: typeof FIRST | typeof LAST) {
    setArmed(true);
    setOpen(true);
    if (next !== undefined) {
      setActive(next);
    }
  }

  function closeList() {
    setOpen(false);
    setActive(undefined);
  }

  /** Shows the selection again instead of edited text (A9). */
  function restore() {
    setDirty(false);
    setText('');
  }

  function choose(item: T) {
    if (isDisabled(item)) {
      return;
    }
    onSelect(item);
    restore();
    if (mode === 'add') {
      setActive(undefined);
    } else {
      closeList();
    }
  }

  function move(step: 1 | -1) {
    if (enabledIds.length === 0) {
      return;
    }
    const current = activeId === undefined ? -1 : enabledIds.indexOf(activeId);
    const next =
      current === -1
        ? step === 1
          ? 0
          : enabledIds.length - 1
        : (current + step + enabledIds.length) % enabledIds.length;
    setActive(enabledIds[next]);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (!open) {
          openList(event.altKey ? undefined : FIRST);
        } else if (!event.altKey) {
          move(1);
        }
        return;
      case 'ArrowUp':
        event.preventDefault();
        if (!open) {
          openList(event.altKey ? undefined : LAST);
        } else if (!event.altKey) {
          move(-1);
        }
        return;
      case 'Enter': {
        // Never submits the form: Enter only chooses (A7).
        event.preventDefault();
        const item = activeIndex >= 0 ? rows[activeIndex] : undefined;
        if (open && item !== undefined) {
          choose(item);
        }
        return;
      }
      case 'Escape':
        if (open) {
          event.preventDefault();
          closeList();
        } else if (dirty) {
          event.preventDefault();
          restore();
        }
        return;
      case 'Tab':
        closeList();
        return;
      default:
        return;
    }
  }

  const describedBy = [
    help ? ids.help : undefined,
    ids.status,
    failed ? ids.failed : undefined,
    errorMessage ? ids.error : undefined,
  ]
    .filter(Boolean)
    .join(' ');

  let options: ReactNode = null;
  if (open) {
    options = rows.map((item, index) => {
      const value = optionId(item);
      const disabled = isDisabled(item);
      const isActive = index === activeIndex;
      const note = optionNote?.(item);
      return (
        <li
          key={value}
          id={optionDomId(index)}
          data-index={index}
          role="option"
          aria-selected={mode === 'single' && value === selected?.id}
          aria-disabled={disabled ? true : undefined}
          className={cn(
            'flex min-h-9 items-center gap-2 rounded-sm px-2 py-1.5 text-sm break-words',
            isActive && 'bg-accent text-accent-foreground',
            mode === 'single' && value === selected?.id && 'font-medium',
            disabled
              ? 'cursor-not-allowed text-muted-foreground'
              : 'cursor-pointer',
            optionClassName,
          )}
          onMouseMove={() => {
            if (!disabled && !isActive) {
              setActive(value);
            }
          }}
          onClick={() => choose(item)}
        >
          <span className="min-w-0 break-words">
            {optionLabel(item)}
            {note ? (
              <span className="text-muted-foreground"> · {note}</span>
            ) : null}
          </span>
        </li>
      );
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <label id={ids.label} htmlFor={ids.field} className="text-sm font-medium">
        {label}
      </label>
      {help ? (
        <p id={ids.help} className="text-sm text-muted-foreground">
          {help}
        </p>
      ) : null}
      <Popover
        open={open}
        onOpenChange={(next) => {
          if (!next) {
            closeList();
          }
        }}
      >
        <PopoverAnchor asChild>
          <div ref={anchorRef} className="relative">
            <Input
              ref={inputRef}
              id={ids.field}
              type="text"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={open}
              aria-controls={open ? ids.listbox : undefined}
              aria-activedescendant={
                open && activeIndex >= 0 ? optionDomId(activeIndex) : undefined
              }
              aria-invalid={errorMessage ? true : undefined}
              aria-describedby={describedBy}
              autoComplete="off"
              spellCheck={false}
              maxLength={SEARCH_MAX_LENGTH}
              autoFocus={autoFocus}
              className={cn('pr-10 text-base md:text-sm', inputClassName)}
              value={shownText}
              onFocus={() => setArmed(true)}
              onClick={() => {
                if (!open) {
                  openList();
                }
              }}
              onChange={(event) => {
                setText(event.target.value);
                setDirty(true);
                setActive(undefined);
                openList();
              }}
              onKeyDown={onKeyDown}
              onBlur={() => {
                closeList();
                restore();
              }}
            />
            <button
              type="button"
              tabIndex={-1}
              aria-label={t('combobox.toggle')}
              aria-controls={open ? ids.listbox : undefined}
              aria-expanded={open}
              className="absolute top-1/2 right-1 flex size-7 -translate-y-1/2 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
              // Keeps DOM focus in the field.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                inputRef.current?.focus();
                if (open) {
                  closeList();
                } else {
                  openList();
                }
              }}
            >
              <ChevronDownIcon aria-hidden="true" className="size-4" />
            </button>
          </div>
        </PopoverAnchor>
        <PopoverContent
          // A plain container: the listbox inside is the popup (no dialog).
          role={undefined}
          align="start"
          sideOffset={4}
          className="flex w-(--radix-popover-trigger-width) flex-col p-1"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          // The field's own Escape closes the list (A7).
          onEscapeKeyDown={(event) => event.preventDefault()}
          // A press in the popup keeps DOM focus in the field (A2).
          onMouseDown={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            if (
              event.target instanceof Node &&
              anchorRef.current?.contains(event.target)
            ) {
              event.preventDefault();
            }
          }}
          // Inside a modal dialog, its scroll lock would swallow the list's
          // wheel and touch scrolling (the popup is portalled out of it).
          onWheel={(event) => event.stopPropagation()}
          onTouchMove={(event) => event.stopPropagation()}
        >
          <ul
            ref={listRef}
            id={ids.listbox}
            role="listbox"
            aria-labelledby={ids.label}
            aria-busy={busy ? true : undefined}
            className="max-h-[min(16rem,50vh)] overflow-y-auto"
          >
            {options}
          </ul>
          {state ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">{state}</p>
          ) : null}
          {footer ? (
            <p className="border-t border-border px-2 py-1.5 text-sm text-muted-foreground">
              {footer}
            </p>
          ) : null}
        </PopoverContent>
      </Popover>
      {failed ? (
        <div className="flex flex-wrap items-center gap-2">
          <p id={ids.failed} className="text-sm text-destructive">
            {t('combobox.failed')}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              if (searching) {
                void searched.refetch();
              } else {
                void first.refetch();
              }
            }}
          >
            {t('combobox.retry')}
          </Button>
        </div>
      ) : null}
      {errorMessage ? (
        <p id={ids.error} className="text-sm text-destructive">
          {errorMessage}
        </p>
      ) : null}
      <p id={ids.status} role="status" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}
