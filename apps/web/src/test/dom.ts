import { act } from 'react';
import { vi } from 'vitest';
import { settle, settleUntil } from './render-app';

// DOM helpers for component tests in jsdom (no Testing Library, #28).

/** The input labelled `text` (a <label for> with exactly that text). */
export function fieldByLabel(root: ParentNode, text: string): HTMLInputElement {
  const label = [...root.querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  );
  const id = label?.htmlFor;
  const field = id ? document.getElementById(id) : null;
  if (!(field instanceof HTMLInputElement)) {
    throw new Error(`No field labelled "${text}"`);
  }
  return field;
}

/** Sets an input's value the way typing does (React sees an input event). */
export function typeInto(input: HTMLInputElement, value: string): void {
  // The prototype's setter, so React's value tracker sees a change.
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** Clicks an element inside act(), then lets fetches and renders settle. */
export async function click(element: HTMLElement): Promise<void> {
  act(() => element.click());
  await settle();
}

/** Presses Escape on the focused element (Radix listens on the document). */
export async function pressEscape(): Promise<void> {
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
  });
  await settle();
}

/** The open dialog (portalled to <body>), or null. */
export function openDialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[role="dialog"]');
}

/** Every element with role="alert" under `root`, as text. */
export function alerts(root: ParentNode = document): string[] {
  return [...root.querySelectorAll('[role="alert"]')].map(
    (element) => element.textContent ?? '',
  );
}

/**
 * Sets a field's value the way typing or choosing does (React sees the
 * event): an input or textarea gets an input event, a select a change
 * event (#308).
 */
export function setFieldValue(
  field: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string,
): void {
  const prototype =
    field instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : field instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
  // The prototype's setter, so React's value tracker sees a change.
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  act(() => {
    setter?.call(field, value);
    field.dispatchEvent(
      new Event(field instanceof HTMLSelectElement ? 'change' : 'input', {
        bubbles: true,
      }),
    );
  });
}

/** The field (input, textarea or select) whose <label> reads `text`. */
export function labelled<
  T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
>(root: ParentNode, text: string): T {
  const label = [...root.querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  );
  const field = label?.htmlFor ? document.getElementById(label.htmlFor) : null;
  if (!(
    field instanceof HTMLInputElement ||
    field instanceof HTMLTextAreaElement ||
    field instanceof HTMLSelectElement
  )) {
    throw new Error(`No field labelled "${text}"`);
  }
  return field as T;
}

/** The options of the open combobox list (portalled to <body>, #545). */
export function comboboxOptions(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('[role="option"]')];
}

/** The open combobox option whose text is `name`, or undefined. */
export function comboboxOption(name: string): HTMLElement | undefined {
  return comboboxOptions().find((option) => option.textContent === name);
}

/**
 * Picks from the combobox labelled `label` (#545): focuses it, types
 * `query`, waits for the option whose text is `name` and clicks it.
 */
export async function pickOption(
  root: ParentNode,
  label: string,
  query: string,
  name: string,
): Promise<void> {
  const field = fieldByLabel(root, label);
  act(() => field.focus());
  typeInto(field, query);
  await settleUntil(() => comboboxOption(name));
  await click(comboboxOption(name)!);
}

/**
 * Types into a field the way a browser does, one input event per key
 * (#598 QA D1, #600 AC2): `\b` is Backspace and `^A\b` selects all and
 * deletes. Each event carries the text as it is after that key.
 */
export function typeKeys(field: HTMLInputElement, keys: string): void {
  let text = field.value;
  for (let i = 0; i < keys.length; i += 1) {
    if (keys.startsWith('^A\b', i)) {
      text = '';
      i += 2;
    } else if (keys[i] === '\b') {
      text = text.slice(0, -1);
    } else {
      text += keys[i];
    }
    typeInto(field, text);
  }
}

/** Presses `key` on `field` (a keydown its handlers see), then settles. */
export async function pressKey(
  field: HTMLElement,
  key: string,
): Promise<KeyboardEvent> {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    field.dispatchEvent(event);
  });
  await settle();
  return event;
}

/** Focuses then blurs `field` (an edit ends, as on Tab), then settles. */
export async function leaveField(field: HTMLElement): Promise<void> {
  act(() => field.focus());
  act(() => field.blur());
  await settle();
}

/**
 * jsdom has no layout: a time list's option sits at 10 px per quarter hour
 * (its `data-time`), and the returned list records every `scrollTop` set
 * (#598 QA D2, #600 AC4). Restore with `vi.restoreAllMocks()`.
 */
export function stubTimeListLayout(): number[] {
  const scrolledTo: number[] = [];
  vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(
    function (this: HTMLElement) {
      const time = this.dataset.time;
      if (!time) {
        return 0;
      }
      const [hours, minutes] = time.split(':').map(Number) as [number, number];
      return ((hours * 60 + minutes) / 15) * 10;
    },
  );
  vi.spyOn(HTMLElement.prototype, 'scrollTop', 'set').mockImplementation(
    (top: number) => {
      scrolledTo.push(top);
    },
  );
  return scrolledTo;
}

/** The px offset `stubTimeListLayout` gives the option at `time`. */
export function stubbedOffset(time: string): number {
  const [hours, minutes] = time.split(':').map(Number) as [number, number];
  return ((hours * 60 + minutes) / 15) * 10;
}
