import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { applyLanguage } from '@/i18n';
import { useFormatters } from './formatters';

(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function Sample() {
  const { formatInstant, formatLocalDate, formatMoney } = useFormatters();
  return (
    <p>
      {formatInstant('2026-10-18T05:30:00Z')} | {formatLocalDate('2026-03-01')}{' '}
      | {formatMoney('1250.00')}
    </p>
  );
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('useFormatters', () => {
  it('binds the active language and the center zone, and follows switches', () => {
    applyLanguage('es');
    act(() => root.render(<Sample />));
    expect(container.textContent).toBe(
      '17 oct 2026, 23:30 | 1 mar 2026 | $1,250.00',
    );

    act(() => applyLanguage('en'));
    expect(container.textContent).toBe(
      'Oct 17, 2026, 11:30 PM | Mar 1, 2026 | $1,250.00',
    );
  });
});
