import { act } from 'react';
import { settle } from './render-app';

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
