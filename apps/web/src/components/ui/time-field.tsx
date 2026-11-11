import { ClockIcon } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { toLanguage } from '@/i18n/language';
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from '@/components/ui/popover';
import {
  formatTime,
  minutesOfDay,
  parseTime,
  timeMatches,
  timeOptions,
} from '@/lib/timeOfDay';
import { cn } from '@/lib/utils';

// A time of day you can type or pick (HF-13, #598 E1 a): the WAI-ARIA APG
// "combobox with list autocomplete" over the existing Input and the pinned
// radix-ui Popover, as `combobox.tsx` (no new dependency). Typing `9`,
// `930`, `9:30`, `09.30`, `21:30` or `9:30 pm` commits `HH:MM` as soon as
// the text reads as a time; text that doesn't commits '' and is kept so the
// form can say why. The list holds the quarter hours in the app language
// (es `09:30`, en `9:30 AM`), filtered by the text, and opens scrolled to
// the chosen time (or `scrollTo`). A time off the grid is shown and kept.
// DOM focus stays in the field: the active option is
// `aria-activedescendant`. The label, error and descriptions are the
// caller's (`id`, `labelId`, `aria-describedby`).

export interface TimeFieldProps {
  /** The input's id (the caller's `<label htmlFor>`). */
  id: string;
  /** The id of the caller's label (also names the list). */
  labelId: string;
  /** `HH:MM`, or '' for no time. */
  value: string;
  /**
   * A new value: `HH:MM`, or '' with `unreadable` when the text isn't a
   * time (empty text is not unreadable).
   */
  onChange: (value: string, unreadable: boolean) => void;
  /**
   * An edit is over (blur, Tab, Enter, Escape or a chosen option): the
   * caller can drop what it kept from before the edit (#598 QA D1).
   */
  onEditEnd?: () => void;
  /** Where the list opens with no time chosen (`HH:MM`); default 08:00. */
  scrollTo?: string | undefined;
  /** Minutes between options; default 15 (the calendar's snap). */
  stepMinutes?: number;
  invalid?: boolean;
  /** No typing and no list (a dialog that is sending, #600). */
  disabled?: boolean | undefined;
  'aria-describedby'?: string | undefined;
  className?: string;
}

/** The time the list opens at with nothing chosen. */
export const DEFAULT_SCROLL_TIME = '08:00';

export function TimeField({
  id,
  labelId,
  value,
  onChange,
  onEditEnd,
  scrollTo,
  stepMinutes = 15,
  invalid,
  disabled = false,
  'aria-describedby': describedBy,
  className,
}: TimeFieldProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const listId = `${id}-listbox`;
  const optionDomId = (option: string) =>
    `${listId}-${option.replace(':', '')}`;
  const anchorRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // Typed text, with the value it committed and the value before typing
  // (Escape goes back to it). It is shown while that value is still the
  // field's (a suggestion or the prefill replaces it).
  const [draft, setDraft] = useState<{
    text: string;
    value: string;
    before: string;
  } | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string | null>(null);

  const typing = draft !== null && draft.value === value;
  const shownText = typing ? draft.text : formatTime(value, language);
  const query = typing ? draft.text : '';
  const options = timeOptions(stepMinutes);
  const rows = options.filter((option) => timeMatches(option, query, language));
  const activeOption = active !== null && rows.includes(active) ? active : null;

  // The option the list opens at: the chosen time (the quarter hour before
  // one off the grid), else `scrollTo`.
  const anchorTime = minutesOfDay(value) !== null ? value : scrollTo;
  const anchorMinutes = minutesOfDay(anchorTime ?? DEFAULT_SCROLL_TIME) ?? 0;
  const anchorOption =
    rows.findLast((option) => (minutesOfDay(option) ?? 0) <= anchorMinutes) ??
    rows[0];
  const shown = open && rows.length > 0 && !disabled;

  // On open, the anchor is at the top of the list. The list is portalled
  // and mounts after the field's render, so the scroll happens when it
  // mounts (#598 QA D2), whichever way it was opened.
  const [listNode, setListNode] = useState<HTMLUListElement | null>(null);
  const attachList = useCallback((node: HTMLUListElement | null) => {
    listRef.current = node;
    setListNode(node);
  }, []);
  const scrollToAnchor = useEffectEvent((node: HTMLUListElement) => {
    const target = activeOption ?? (query === '' ? anchorOption : undefined);
    const element =
      target === undefined
        ? null
        : node.querySelector<HTMLElement>(`[data-time="${target}"]`);
    if (element) {
      node.scrollTop = element.offsetTop;
    }
  });
  useLayoutEffect(() => {
    if (listNode) {
      scrollToAnchor(listNode);
    }
  }, [listNode]);

  // Then the active option scrolls into view.
  useEffect(() => {
    if (!shown || activeOption === null) {
      return;
    }
    listRef.current
      ?.querySelector<HTMLElement>(`[data-time="${activeOption}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
  }, [shown, activeOption]);

  function closeList() {
    setOpen(false);
    setActive(null);
  }

  /** Closes the list and ends the edit (the caller's baseline goes). */
  function finish() {
    closeList();
    onEditEnd?.();
  }

  /** Shows the committed value again in the language's format. */
  function settle() {
    if (draft === null) {
      return;
    }
    // Unreadable text stays, so the form can say what's wrong with it.
    if (!typing || draft.text.trim() === '' || parseTime(draft.text) !== null) {
      setDraft(null);
    }
  }

  function type(text: string) {
    const parsed = parseTime(text);
    const next = parsed ?? '';
    setDraft({
      text,
      value: next,
      before: typing && draft !== null ? draft.before : value,
    });
    setActive(null);
    setOpen(true);
    onChange(next, parsed === null && text.trim() !== '');
  }

  function choose(option: string) {
    setDraft(null);
    onChange(option, false);
    finish();
  }

  function move(step: 1 | -1) {
    if (rows.length === 0) {
      return;
    }
    const from = activeOption ?? (query === '' ? anchorOption : undefined);
    const index = from === undefined ? -1 : rows.indexOf(from);
    let next: number;
    if (activeOption === null) {
      next = index >= 0 ? index : step === 1 ? 0 : rows.length - 1;
    } else {
      next = (index + step + rows.length) % rows.length;
    }
    setActive(rows[next] ?? null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault();
        setOpen(true);
        move(event.key === 'ArrowDown' ? 1 : -1);
        return;
      case 'Enter':
        if (shown && activeOption !== null) {
          event.preventDefault();
          choose(activeOption);
        } else if (open || typing) {
          event.preventDefault();
          finish();
          settle();
        }
        return;
      case 'Escape':
        if (open || typing) {
          event.preventDefault();
          if (typing && draft !== null) {
            // Back to the time before typing.
            onChange(draft.before, false);
          }
          setDraft(null);
          finish();
        }
        return;
      case 'Tab':
        finish();
        settle();
        return;
      default:
        return;
    }
  }

  return (
    <Popover
      open={shown}
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
            id={id}
            type="text"
            role="combobox"
            inputMode="text"
            aria-autocomplete="list"
            aria-expanded={shown}
            aria-controls={shown ? listId : undefined}
            aria-activedescendant={
              shown && activeOption !== null
                ? optionDomId(activeOption)
                : undefined
            }
            aria-invalid={invalid ? true : undefined}
            aria-describedby={describedBy}
            disabled={disabled}
            autoComplete="off"
            spellCheck={false}
            maxLength={16}
            className={cn('pr-10', className)}
            value={shownText}
            onClick={() => {
              if (!disabled) {
                setOpen(true);
              }
            }}
            onChange={(event) => type(event.target.value)}
            onKeyDown={onKeyDown}
            onBlur={() => {
              finish();
              settle();
            }}
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label={t('combobox.toggle')}
            aria-controls={shown ? listId : undefined}
            aria-expanded={shown}
            disabled={disabled}
            className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-md text-muted-foreground hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
            // Keeps DOM focus in the field.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              inputRef.current?.focus();
              if (shown) {
                closeList();
              } else {
                setOpen(true);
              }
            }}
          >
            <ClockIcon aria-hidden="true" className="size-4" />
          </button>
        </div>
      </PopoverAnchor>
      <PopoverContent
        // A plain container: the listbox inside is the popup (no dialog).
        role={undefined}
        align="start"
        sideOffset={4}
        className="flex w-(--radix-popover-trigger-width) min-w-32 flex-col p-1"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        // The field's own Escape closes the list.
        onEscapeKeyDown={(event) => event.preventDefault()}
        // A press in the popup keeps DOM focus in the field.
        onMouseDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => {
          if (
            event.target instanceof Node &&
            anchorRef.current?.contains(event.target)
          ) {
            event.preventDefault();
          }
        }}
        onWheel={(event) => event.stopPropagation()}
        onTouchMove={(event) => event.stopPropagation()}
      >
        <ul
          ref={attachList}
          id={listId}
          role="listbox"
          aria-labelledby={labelId}
          className="relative max-h-[min(16rem,50vh)] overflow-y-auto"
        >
          {shown
            ? rows.map((option) => (
                <li
                  key={option}
                  id={optionDomId(option)}
                  data-time={option}
                  role="option"
                  aria-selected={option === value}
                  className={cn(
                    // 44 px targets under 768 px (#444 D3).
                    'flex min-h-11 cursor-pointer items-center rounded-sm px-2 py-1.5 text-sm tabular-nums md:min-h-9',
                    option === activeOption &&
                      'bg-accent text-accent-foreground',
                    option === value && 'font-medium',
                  )}
                  onMouseMove={() => {
                    if (option !== activeOption) {
                      setActive(option);
                    }
                  }}
                  onClick={() => choose(option)}
                >
                  {formatTime(option, language)}
                </li>
              ))
            : null}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
