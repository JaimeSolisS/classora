import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { settle } from '@/test/render-app';
import { TimeField, type TimeFieldProps } from './time-field';

// HF-13 (#598 E1 a, AC4, AC5, AC9): the type-or-pick time field in jsdom
// (no Testing Library, #28).

let container: HTMLDivElement;
let root: Root;
const changes = vi.fn<(value: string, unreadable: boolean) => void>();
const submit = vi.fn();

function Harness({
  initial = '',
  ...rest
}: Partial<TimeFieldProps> & { initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label id="start-label" htmlFor="start">
        Start
      </label>
      <TimeField
        id="start"
        labelId="start-label"
        value={value}
        onChange={(next, unreadable) => {
          changes(next, unreadable);
          setValue(next);
        }}
        {...rest}
      />
      <output data-testid="value">{value}</output>
      <button type="submit">Save</button>
    </form>
  );
}

function render(props: Parameters<typeof Harness>[0] = {}) {
  act(() => root.render(<Harness {...props} />));
}

const field = () => container.querySelector<HTMLInputElement>('#start')!;
const value = () =>
  container.querySelector('[data-testid="value"]')!.textContent;
const listbox = () => document.querySelector<HTMLElement>('[role="listbox"]');
const options = () => [
  ...document.querySelectorAll<HTMLElement>('[role="option"]'),
];
const activeOption = () => {
  const id = field().getAttribute('aria-activedescendant');
  return id ? document.getElementById(id)?.textContent : undefined;
};

async function type(text: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(field(), text);
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
  await settle();
}

async function press(key: string) {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    field().dispatchEvent(event);
  });
  await settle();
  return event;
}

async function focus() {
  act(() => field().focus());
  await settle();
}

async function blur() {
  act(() => field().blur());
  await settle();
}

beforeEach(() => {
  applyLanguage('en');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  changes.mockReset();
  submit.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('TimeField: typing (AC4)', () => {
  it('commits HH:MM as soon as the text reads as a time, then shows it formatted', async () => {
    render();
    await focus();
    await type('9:30 pm');
    expect(value()).toBe('21:30');
    expect(changes).toHaveBeenLastCalledWith('21:30', false);
    expect(field().value).toBe('9:30 pm');
    await blur();
    expect(field().value).toBe('9:30 PM');
  });

  it('commits nothing for unreadable text and keeps the text', async () => {
    render({ initial: '10:00' });
    await focus();
    await type('25:00');
    expect(value()).toBe('');
    expect(changes).toHaveBeenLastCalledWith('', true);
    await blur();
    expect(field().value).toBe('25:00');
    // Emptying it is not unreadable.
    await type('');
    expect(changes).toHaveBeenLastCalledWith('', false);
  });

  it('keeps and shows a time off the quarter-hour grid', async () => {
    render();
    await focus();
    await type('10:10');
    await blur();
    expect(value()).toBe('10:10');
    expect(field().value).toBe('10:10 AM');
  });
});

describe('TimeField: the list (AC5, AC9)', () => {
  it('is a labelled combobox that lists the quarter hours in the language', async () => {
    render();
    expect(field().getAttribute('role')).toBe('combobox');
    expect(field().getAttribute('aria-expanded')).toBe('false');
    await focus();
    act(() => field().click());
    await settle();
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(field().getAttribute('aria-controls')).toBe(listbox()!.id);
    expect(listbox()!.getAttribute('aria-labelledby')).toBe('start-label');
    expect(options()).toHaveLength(96);
    expect(options()[38]!.textContent).toBe('9:30 AM');
    // 44 px targets under 768 px.
    expect(options()[0]!.className).toContain('min-h-11');
  });

  it('labels the options 24-hour in Spanish', async () => {
    applyLanguage('es');
    render({ initial: '21:30' });
    expect(field().value).toBe('21:30');
    await focus();
    act(() => field().click());
    await settle();
    expect(options()[38]!.textContent).toBe('09:30');
  });

  it('filters the list by the typed text', async () => {
    render();
    await focus();
    await type('9:3');
    expect(options().map((option) => option.textContent)).toEqual([
      '9:30 AM',
      '9:30 PM',
    ]);
  });

  it('sets HH:MM when an option is clicked', async () => {
    render();
    await focus();
    act(() => field().click());
    await settle();
    act(() => options()[38]!.click());
    await settle();
    expect(value()).toBe('09:30');
    expect(field().value).toBe('9:30 AM');
    expect(listbox()).toBeNull();
  });
});

describe('TimeField: the keyboard (AC5)', () => {
  it('opens at the chosen time, moves, and chooses with Enter (no submit)', async () => {
    render({ initial: '10:00' });
    await focus();
    await press('ArrowDown');
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(activeOption()).toBe('10:00 AM');
    await press('ArrowDown');
    expect(activeOption()).toBe('10:15 AM');
    await press('ArrowUp');
    await press('ArrowUp');
    expect(activeOption()).toBe('9:45 AM');
    const enter = await press('Enter');
    expect(enter.defaultPrevented).toBe(true);
    expect(value()).toBe('09:45');
    expect(submit).not.toHaveBeenCalled();
    expect(field().getAttribute('aria-expanded')).toBe('false');
  });

  it('opens at `scrollTo`, else 08:00, with no time chosen', async () => {
    render({ scrollTo: '11:00' });
    await focus();
    await press('ArrowDown');
    expect(activeOption()).toBe('11:00 AM');
    act(() => root.render(<Harness key="other" />));
    await focus();
    await press('ArrowDown');
    expect(activeOption()).toBe('8:00 AM');
  });

  it('opens at the quarter hour before a time off the grid', async () => {
    render({ initial: '10:10' });
    await focus();
    await press('ArrowDown');
    expect(activeOption()).toBe('10:00 AM');
  });

  it('closes with Escape and goes back to the time before typing', async () => {
    render({ initial: '10:00' });
    await focus();
    await type('1');
    await type('14');
    expect(value()).toBe('14:00');
    await press('Escape');
    expect(field().getAttribute('aria-expanded')).toBe('false');
    expect(value()).toBe('10:00');
    expect(field().value).toBe('10:00 AM');
    expect(changes).toHaveBeenLastCalledWith('10:00', false);
  });

  it('commits the typed time on Tab', async () => {
    render();
    await focus();
    await type('930');
    await press('Tab');
    expect(value()).toBe('09:30');
    expect(field().value).toBe('9:30 AM');
  });

  it('lets Enter submit the form when nothing is open or typed', async () => {
    render({ initial: '10:00' });
    await focus();
    const enter = await press('Enter');
    expect(enter.defaultPrevented).toBe(false);
  });
});

describe('TimeField: the list opens scrolled (#598 QA D2)', () => {
  // jsdom has no layout: each option sits at 10 px per quarter hour, and
  // the list records where it was scrolled to.
  let scrolledTo: number[];
  beforeEach(() => {
    scrolledTo = [];
    vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(
      function (this: HTMLElement) {
        const time = this.dataset.time;
        if (!time) {
          return 0;
        }
        const [hours, minutes] = time.split(':').map(Number) as [
          number,
          number,
        ];
        return ((hours * 60 + minutes) / 15) * 10;
      },
    );
    vi.spyOn(HTMLElement.prototype, 'scrollTop', 'set').mockImplementation(
      (top: number) => {
        scrolledTo.push(top);
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function openByClick() {
    await focus();
    act(() => field().click());
    await settle();
  }

  it('opens at the chosen time on a click', async () => {
    render({ initial: '10:00' });
    await openByClick();
    expect(listbox()).not.toBeNull();
    expect(scrolledTo.at(-1)).toBe(400);
  });

  it('opens at `scrollTo` (start + 1 h for the end), else 08:00', async () => {
    render({ scrollTo: '11:00' });
    await openByClick();
    expect(scrolledTo.at(-1)).toBe(440);
    act(() => root.render(<Harness key="other" />));
    await openByClick();
    expect(scrolledTo.at(-1)).toBe(320);
  });

  it('opens at the quarter hour before a time off the grid', async () => {
    render({ initial: '10:10' });
    await openByClick();
    expect(scrolledTo.at(-1)).toBe(400);
  });

  it('opens scrolled from the clock button too', async () => {
    render({ initial: '16:45' });
    const toggle = container.querySelector<HTMLButtonElement>(
      'button[type="button"]',
    )!;
    act(() => toggle.click());
    await settle();
    expect(listbox()).not.toBeNull();
    expect(scrolledTo.at(-1)).toBe(670);
  });
});

describe('TimeField: one edit per focus (#598 QA D1)', () => {
  it('ends the edit on blur, Tab, Enter, Escape and a chosen option', async () => {
    const ended = vi.fn();
    render({ initial: '10:00', onEditEnd: ended });
    await focus();
    await type('11');
    await press('Tab');
    expect(ended).toHaveBeenCalledTimes(1);
    await type('12');
    await press('Escape');
    expect(ended).toHaveBeenCalledTimes(2);
    act(() => field().click());
    await settle();
    act(() => options()[0]!.click());
    await settle();
    expect(ended).toHaveBeenCalledTimes(3);
    await blur();
    expect(ended).toHaveBeenCalledTimes(4);
  });
});
