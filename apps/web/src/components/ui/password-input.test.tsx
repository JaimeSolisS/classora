import { act, createRef, type Ref } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { typeInto } from '@/test/dom';
import { PasswordInput } from './password-input';

// #664: the shared password field with a show/hide toggle, in jsdom (no
// Testing Library, #28).

let container: HTMLDivElement;
let root: Root;
const submit = vi.fn();

function Harness({
  inputRef,
  disabled,
}: {
  inputRef?: Ref<HTMLInputElement>;
  disabled?: boolean;
}) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor="password">Password</label>
      <PasswordInput
        id="password"
        autoComplete="current-password"
        ref={inputRef}
        disabled={disabled}
      />
      <label htmlFor="confirm">Confirm password</label>
      <PasswordInput id="confirm" autoComplete="new-password" />
      <button type="submit">Save</button>
    </form>
  );
}

function render(props: Parameters<typeof Harness>[0] = {}) {
  act(() => root.render(<Harness {...props} />));
}

const field = (id = 'password') =>
  container.querySelector<HTMLInputElement>(`#${id}`)!;
const toggle = (id = 'password') =>
  container.querySelector<HTMLButtonElement>(`button[aria-controls="${id}"]`)!;
const submitButton = () =>
  container.querySelector<HTMLButtonElement>('button[type="submit"]')!;

function click(element: HTMLElement) {
  act(() => element.click());
}

/** A key press on a button: keydown, keyup and the click a browser fires. */
function press(element: HTMLElement, key: 'Enter' | ' ') {
  act(() => {
    element.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
    );
    element.dispatchEvent(
      new KeyboardEvent('keyup', { key, bubbles: true, cancelable: true }),
    );
    element.click();
  });
}

beforeEach(() => {
  applyLanguage('en');
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  submit.mockReset();
  applyLanguage('en');
});

describe('PasswordInput (#664)', () => {
  it('starts hidden, with the "Show password" name and a hidden icon', () => {
    render();

    expect(field().type).toBe('password');
    expect(toggle().getAttribute('aria-label')).toBe('Show password');
    expect(toggle().getAttribute('type')).toBe('button');
    expect(toggle().hasAttribute('aria-pressed')).toBe(false);
    expect(toggle().getAttribute('aria-controls')).toBe('password');
    const icon = toggle().querySelector('svg');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
  });

  it('a click toggles the type and the name, keeping value and autocomplete', () => {
    render();
    typeInto(field(), 'my-secret-value');

    click(toggle());
    expect(field().type).toBe('text');
    expect(toggle().getAttribute('aria-label')).toBe('Hide password');
    expect(field().value).toBe('my-secret-value');
    expect(field().getAttribute('autocomplete')).toBe('current-password');

    click(toggle());
    expect(field().type).toBe('password');
    expect(toggle().getAttribute('aria-label')).toBe('Show password');
    expect(field().value).toBe('my-secret-value');
    expect(field().getAttribute('autocomplete')).toBe('current-password');
  });

  it('Enter and Space toggle it, focus stays on the button and nothing submits', () => {
    render();
    act(() => toggle().focus());

    press(toggle(), 'Enter');
    expect(field().type).toBe('text');
    expect(document.activeElement).toBe(toggle());

    press(toggle(), ' ');
    expect(field().type).toBe('password');
    expect(document.activeElement).toBe(toggle());
    expect(submit).not.toHaveBeenCalled();
  });

  it('the input keeps only its <label> as its name', () => {
    render();

    expect(field().getAttribute('aria-label')).toBeNull();
    expect(field().labels?.[0]?.textContent).toBe('Password');
    expect(field().labels).toHaveLength(1);
  });

  it('follows the language and keeps the value and visibility', () => {
    render();
    typeInto(field(), 'my-secret-value');
    click(toggle());

    act(() => applyLanguage('es'));
    expect(toggle().getAttribute('aria-label')).toBe('Ocultar contraseña');
    expect(field().type).toBe('text');
    expect(field().value).toBe('my-secret-value');

    click(toggle());
    expect(toggle().getAttribute('aria-label')).toBe('Mostrar contraseña');
  });

  it('two fields toggle independently', () => {
    render();

    click(toggle('confirm'));
    expect(field('confirm').type).toBe('text');
    expect(field('password').type).toBe('password');
  });

  it('every field is hidden again when the form submits', () => {
    render();
    click(toggle('password'));
    click(toggle('confirm'));

    click(submitButton());

    expect(submit).toHaveBeenCalledTimes(1);
    expect(field('password').type).toBe('password');
    expect(field('confirm').type).toBe('password');
    expect(toggle('password').getAttribute('aria-label')).toBe('Show password');
  });

  it('passes the ref to the <input>', () => {
    const inputRef = createRef<HTMLInputElement>();
    render({ inputRef });

    expect(inputRef.current).toBe(field());
  });

  it('a callback ref gets the <input>', () => {
    const inputRef = vi.fn<(node: HTMLInputElement | null) => void>();
    render({ inputRef });

    expect(inputRef).toHaveBeenCalledWith(field());
  });

  it('a disabled field disables its toggle', () => {
    render({ disabled: true });

    expect(toggle().disabled).toBe(true);
    expect(toggle('confirm').disabled).toBe(false);
  });

  it('starts hidden again on a new mount', () => {
    render();
    click(toggle());
    act(() => root.unmount());
    root = createRoot(container);

    render();
    expect(field().type).toBe('password');
  });
});
