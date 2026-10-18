import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/app/api';
import { applyLanguage } from '@/i18n';
import { ErrorAlert } from './ErrorAlert';

(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function render(element: React.ReactNode) {
  act(() => root.render(element));
}

const notFound = new ApiError({
  code: 'not_found',
  status: 404,
  requestId: 'req-abc-123',
  problem: {
    type: 'about:blank',
    title: 'Not Found',
    status: 404,
    code: 'not_found',
    request_id: 'req-abc-123',
    detail: 'SERVER DETAIL',
  },
});

beforeEach(() => {
  applyLanguage('en');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('ErrorAlert', () => {
  it('shows the message and the reference ID in a monospace element', () => {
    render(<ErrorAlert error={notFound} />);

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain(
      "We couldn't find what you were looking for.",
    );
    expect(alert?.textContent).toContain('Reference: req-abc-123');
    const code = alert?.querySelector('code');
    expect(code?.textContent).toBe('req-abc-123');
    expect(code?.className).toContain('font-mono');
    expect(alert?.textContent).not.toContain('SERVER DETAIL');
    expect(alert?.textContent).not.toContain('Not Found');
    expect(alert?.querySelector('svg')?.getAttribute('aria-hidden')).toBe(
      'true',
    );
  });

  it('shows no reference line without a request ID', () => {
    render(<ErrorAlert error={new ApiError({ code: 'network_error' })} />);

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Can't reach the server.");
    expect(alert?.textContent).not.toMatch(/Reference|undefined/);
    expect(alert?.querySelector('code')).toBeNull();
  });

  it('shows the generic message for a value that is not an ApiError', () => {
    render(<ErrorAlert error={new Error('internal stack detail')} />);

    expect(container.textContent).toContain(
      'Something went wrong. Please try again.',
    );
    expect(container.textContent).not.toContain('internal stack detail');
  });

  it('calls onRetry from the "Try again" button, shown only with onRetry', () => {
    render(<ErrorAlert error={notFound} />);
    expect(container.querySelector('button')).toBeNull();

    const onRetry = vi.fn();
    render(<ErrorAlert error={notFound} onRetry={onRetry} />);
    const button = container.querySelector('button');
    expect(button?.textContent).toBe('Try again');
    act(() => button?.click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('switches the message and reference label in place with the language', () => {
    render(<ErrorAlert error={notFound} onRetry={() => {}} />);
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Reference: req-abc-123');

    act(() => applyLanguage('es'));

    expect(container.querySelector('[role="alert"]')).toBe(alert);
    expect(alert?.textContent).toContain('No encontramos lo que buscas.');
    expect(alert?.textContent).toContain('Referencia: req-abc-123');
    expect(alert?.querySelector('button')?.textContent).toBe('Reintentar');
  });
});
