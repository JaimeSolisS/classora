import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { settle } from '@/test/render-app';
import {
  Combobox,
  PICKER_DEBOUNCE_MS,
  type ComboboxProps,
  type ComboboxSelection,
  type OptionsPage,
} from './combobox';

// #545 D1: the type-ahead combobox in jsdom (no Testing Library, #28).

interface Item {
  id: string;
  name: string;
}

const JOSE: Item = { id: 'jose', name: 'José Pérez' };
const ANA: Item = { id: 'ana', name: 'Ana Ñúñez' };
const LUIS: Item = { id: 'luis', name: 'Luis Gómez' };
const ITEMS = [ANA, JOSE, LUIS];

type Load = (
  q: string | null,
  signal: AbortSignal,
) => Promise<OptionsPage<Item>>;

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
const submit = vi.fn();
const selectSpy = vi.fn();

function page(items: Item[], total = items.length): OptionsPage<Item> {
  return { items, total };
}

function Harness({
  load,
  initial = null,
  ...rest
}: Partial<ComboboxProps<Item>> & {
  load: Load;
  initial?: ComboboxSelection | null;
}) {
  const [selected, setSelected] = useState(initial);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Combobox<Item>
        label="Student"
        queryKey={['combobox-test']}
        load={load}
        optionId={(item) => item.id}
        optionLabel={(item) => item.name}
        selected={selected}
        onSelect={(item) => {
          selectSpy(item);
          if (rest.mode !== 'add') {
            setSelected({ id: item.id, label: item.name });
          }
        }}
        {...rest}
      />
      <button type="submit">Save</button>
    </form>
  );
}

function render(props: Parameters<typeof Harness>[0]) {
  act(() =>
    root.render(
      <QueryClientProvider client={client}>
        <Harness {...props} />
      </QueryClientProvider>,
    ),
  );
}

const field = () =>
  container.querySelector<HTMLInputElement>('input[role="combobox"]')!;
const listbox = () => document.querySelector<HTMLElement>('[role="listbox"]');
const options = () => [
  ...document.querySelectorAll<HTMLElement>('[role="option"]'),
];
const optionNames = () => options().map((option) => option.textContent);
const status = () =>
  container.querySelector('[role="status"]')?.textContent ?? '';

async function focus() {
  act(() => field().focus());
  await settle();
}

async function type(value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(field(), value);
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
  await settle();
}

async function press(key: string, init: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  act(() => {
    field().dispatchEvent(event);
  });
  await settle();
  return event;
}

async function waitDebounce() {
  await act(async () => {
    await new Promise((resolve) =>
      setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
    );
  });
  await settle();
}

const activeOption = () => {
  const id = field().getAttribute('aria-activedescendant');
  return id ? document.getElementById(id)?.textContent : undefined;
};

beforeEach(() => {
  applyLanguage('en');
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  submit.mockReset();
  selectSpy.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
});

describe('Combobox: opening', () => {
  it('requests the first page on focus without opening (Tab, autoFocus)', async () => {
    const load = vi.fn<Load>(() => Promise.resolve(page(ITEMS)));
    render({ load });
    expect(load).not.toHaveBeenCalled();
    await focus();
    expect(load).toHaveBeenCalledTimes(1);
    expect(load.mock.calls[0]![0]).toBeNull();
    expect(field().getAttribute('aria-expanded')).toBe('false');
    expect(listbox()).toBeNull();
  });

  it('opens on a click and lists the options without typing', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    act(() => field().click());
    await settle();
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(field().getAttribute('aria-controls')).toBe(listbox()!.id);
    expect(optionNames()).toEqual(['Ana Ñúñez', 'José Pérez', 'Luis Gómez']);
    expect(listbox()!.getAttribute('aria-labelledby')).toBe(
      container.querySelector('label')!.id,
    );
    expect(status()).toBe('3 results');
  });

  it('opens on ArrowDown with the first option active', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await press('ArrowDown');
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(activeOption()).toBe('Ana Ñúñez');
  });

  it('opens on Alt+ArrowDown with no active option', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await press('ArrowDown', { altKey: true });
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(field().hasAttribute('aria-activedescendant')).toBe(false);
  });

  it('opens from the chevron button, which is out of the tab order', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    const toggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Show options"]',
    )!;
    expect(toggle.tabIndex).toBe(-1);
    act(() => toggle.click());
    await settle();
    expect(document.activeElement).toBe(field());
    expect(options()).toHaveLength(3);
  });
});

describe('Combobox: filtering', () => {
  it('filters locally on each keystroke, ignoring accents, with no request', async () => {
    const load = vi.fn<Load>(() => Promise.resolve(page(ITEMS)));
    render({ load });
    await focus();
    await type('jose');
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(optionNames()).toEqual(['José Pérez']);
    await type('nunez');
    expect(optionNames()).toEqual(['Ana Ñúñez']);
    await type('perez jose');
    expect(optionNames()).toEqual(['José Pérez']);
    await waitDebounce();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('matches optionSearchText when given', async () => {
    render({
      load: () => Promise.resolve(page(ITEMS)),
      optionLabel: (item) => `${item.name} (enrolled)`,
      optionSearchText: (item) => item.name,
    });
    await focus();
    await type('enrolled');
    expect(options()).toHaveLength(0);
    expect(status()).toBe('No matches for “enrolled”.');
  });

  it('never sends a blank q', async () => {
    const load = vi.fn<Load>(() => Promise.resolve(page(ITEMS, 300)));
    render({ load });
    await focus();
    await type('jo');
    await type('   ');
    await waitDebounce();
    await type('');
    await waitDebounce();
    expect(load.mock.calls.map(([q]) => q)).toEqual([null]);
    expect(optionNames()).toEqual(['Ana Ñúñez', 'José Pérez', 'Luis Gómez']);
  });

  it('sends the trimmed text after the pause when the first page is partial', async () => {
    const load = vi.fn<Load>((q) =>
      Promise.resolve(q === null ? page(ITEMS, 300) : page([LUIS], 1)),
    );
    render({ load });
    await focus();
    await type(' gom ');
    // Before the pause: the loaded options, filtered locally, busy.
    expect(load).toHaveBeenCalledTimes(1);
    expect(optionNames()).toEqual(['Luis Gómez']);
    expect(listbox()!.getAttribute('aria-busy')).toBe('true');
    await waitDebounce();
    expect(load.mock.calls.map(([q]) => q)).toEqual([null, 'gom']);
    expect(optionNames()).toEqual(['Luis Gómez']);
    expect(listbox()!.hasAttribute('aria-busy')).toBe(false);
  });

  it('ignores a slower earlier response', async () => {
    const pending = new Map<string, (value: OptionsPage<Item>) => void>();
    const load = vi.fn<Load>((q) =>
      q === null
        ? Promise.resolve(page(ITEMS, 300))
        : new Promise((resolve) => pending.set(q, resolve)),
    );
    render({ load });
    await focus();
    await type('a');
    await waitDebounce();
    await type('an');
    await waitDebounce();
    expect([...pending.keys()]).toEqual(['a', 'an']);
    await act(async () => {
      pending.get('an')!(page([ANA], 1));
      await Promise.resolve();
    });
    await settle();
    await act(async () => {
      pending.get('a')!(page([LUIS, JOSE], 2));
      await Promise.resolve();
    });
    await settle();
    expect(optionNames()).toEqual(['Ana Ñúñez']);
  });
});

describe('Combobox: keyboard', () => {
  it('moves with the arrows, wrapping and skipping disabled options', async () => {
    render({
      load: () => Promise.resolve(page(ITEMS)),
      optionDisabled: (item) => item.id === 'jose',
    });
    await focus();
    await press('ArrowDown');
    expect(activeOption()).toBe('Ana Ñúñez');
    await press('ArrowDown');
    expect(activeOption()).toBe('Luis Gómez');
    await press('ArrowDown');
    expect(activeOption()).toBe('Ana Ñúñez');
    await press('ArrowUp');
    expect(activeOption()).toBe('Luis Gómez');
    const disabled = options().find((o) => o.textContent === 'José Pérez')!;
    expect(disabled.getAttribute('aria-disabled')).toBe('true');
  });

  it('selects with Enter without submitting the form', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await type('luis');
    await press('ArrowDown');
    const enter = await press('Enter');
    expect(enter.defaultPrevented).toBe(true);
    expect(selectSpy).toHaveBeenCalledWith(LUIS);
    expect(field().value).toBe('Luis Gómez');
    expect(field().getAttribute('aria-expanded')).toBe('false');
    expect(submit).not.toHaveBeenCalled();
    // Reopened, the choice is marked selected.
    act(() => field().click());
    await settle();
    const chosen = options().find((o) => o.textContent === 'Luis Gómez')!;
    expect(chosen.getAttribute('aria-selected')).toBe('true');
    expect(options()[0]!.getAttribute('aria-selected')).toBe('false');
  });

  it('does nothing on Enter with no active option', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await type('a');
    const enter = await press('Enter');
    expect(enter.defaultPrevented).toBe(true);
    expect(selectSpy).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    expect(field().getAttribute('aria-expanded')).toBe('true');
  });

  it('closes on Escape, then restores the text on a second Escape', async () => {
    render({
      load: () => Promise.resolve(page(ITEMS)),
      initial: { id: 'ana', label: 'Ana Ñúñez' },
    });
    await focus();
    await type('xyz');
    await press('Escape');
    expect(field().getAttribute('aria-expanded')).toBe('false');
    expect(field().value).toBe('xyz');
    await press('Escape');
    expect(field().value).toBe('Ana Ñúñez');
  });

  it('closes on Tab without selecting, and leaving restores the text', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await type('jo');
    await press('ArrowDown');
    await press('Tab');
    expect(field().getAttribute('aria-expanded')).toBe('false');
    expect(selectSpy).not.toHaveBeenCalled();
    act(() => field().blur());
    await settle();
    expect(field().value).toBe('');
  });
});

describe('Combobox: pointer, ARIA and modes', () => {
  it('selects an enabled option on click; a disabled one does nothing', async () => {
    render({
      load: () => Promise.resolve(page(ITEMS)),
      optionDisabled: (item) => item.id === 'jose',
      optionNote: (item) =>
        item.id === 'jose' ? 'already enrolled' : undefined,
    });
    await focus();
    act(() => field().click());
    await settle();
    const jose = options().find((o) => o.textContent?.startsWith('José'))!;
    expect(jose.textContent).toBe('José Pérez · already enrolled');
    act(() => jose.click());
    await settle();
    expect(selectSpy).not.toHaveBeenCalled();
    act(() => options()[0]!.click());
    await settle();
    expect(selectSpy).toHaveBeenCalledWith(ANA);
    expect(field().value).toBe('Ana Ñúñez');
    expect(listbox()).toBeNull();
  });

  it('is labelled, autocomplete-off and described by its help and error', () => {
    render({
      load: () => Promise.resolve(page(ITEMS)),
      help: 'Search by name.',
      errorMessage: 'Choose a student.',
    });
    const input = field();
    expect(input.getAttribute('aria-autocomplete')).toBe('list');
    expect(input.getAttribute('autocomplete')).toBe('off');
    expect(input.getAttribute('spellcheck')).toBe('false');
    expect(input.maxLength).toBe(100);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(input.hasAttribute('aria-controls')).toBe(false);
    const described = input
      .getAttribute('aria-describedby')!
      .split(' ')
      .map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain('Search by name.');
    expect(described).toContain('Choose a student.');
    const label = container.querySelector('label')!;
    expect(label.htmlFor).toBe(input.id);
  });

  it('add mode reports the choice, clears the text and stays open', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)), mode: 'add' });
    await focus();
    await type('ana');
    await press('ArrowDown');
    await press('Enter');
    expect(selectSpy).toHaveBeenCalledWith(ANA);
    expect(field().value).toBe('');
    expect(document.activeElement).toBe(field());
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(options()).toHaveLength(3);
    expect(options()[0]!.getAttribute('aria-selected')).toBe('false');
  });

  it('shows a selection set from outside', () => {
    render({
      load: () => Promise.resolve(page(ITEMS)),
      initial: { id: 'luis', label: 'Luis Gómez' },
    });
    expect(field().value).toBe('Luis Gómez');
  });

  it('keeps the typed text across a language switch', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await type('lu');
    act(() => applyLanguage('es'));
    await settle();
    expect(field().value).toBe('lu');
    expect(
      container.querySelector('button[aria-label="Mostrar opciones"]'),
    ).not.toBeNull();
    expect(status()).toBe('1 resultado');
  });
});

describe('Combobox: states', () => {
  it('shows loading while the first page is pending', async () => {
    render({ load: () => new Promise(() => {}) });
    await focus();
    act(() => field().click());
    await settle();
    expect(listbox()!.getAttribute('aria-busy')).toBe('true');
    expect(status()).toBe('Loading…');
    expect(document.body.textContent).toContain('Loading…');
  });

  it('shows the caller’s empty text when the source is empty', async () => {
    render({
      load: () => Promise.resolve(page([])),
      emptyText: 'There are no students to choose from.',
    });
    await focus();
    act(() => field().click());
    await settle();
    expect(status()).toBe('There are no students to choose from.');
  });

  it('shows no results for text that matches nothing', async () => {
    render({ load: () => Promise.resolve(page(ITEMS)) });
    await focus();
    await type('zzz');
    expect(status()).toBe('No matches for “zzz”.');
    expect(document.body.textContent).toContain('No matches for “zzz”.');
  });

  it('shows the truncation footer when the source holds more', async () => {
    render({ load: () => Promise.resolve(page(ITEMS, 312)) });
    await focus();
    act(() => field().click());
    await settle();
    const footer = 'Showing 3 of 312. Keep typing to narrow the list.';
    expect(document.body.textContent).toContain(footer);
    expect(status()).toBe(`3 results ${footer}`);
  });

  it('shows the error under the field with a retry that refetches', async () => {
    let fail = true;
    const load = vi.fn<Load>(() =>
      fail ? Promise.reject(new Error('down')) : Promise.resolve(page(ITEMS)),
    );
    render({ load });
    await focus();
    expect(status()).toBe("Couldn't load the options.");
    const retry = [...container.querySelectorAll('button')].find(
      (button) => button.textContent === 'Try again',
    )!;
    expect(field().getAttribute('aria-describedby')).toContain(
      container.querySelector('.text-destructive')!.id,
    );
    fail = false;
    act(() => retry.click());
    await settle();
    expect(load).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain('Try again');
    act(() => field().focus());
    act(() => field().click());
    await settle();
    expect(options()).toHaveLength(3);
  });
});
