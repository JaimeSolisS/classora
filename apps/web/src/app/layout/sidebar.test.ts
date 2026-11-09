import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  readSidebarCollapsed,
  SIDEBAR_STORAGE_KEY,
  writeSidebarCollapsed,
} from './sidebar';

// #546 B5/H2: the collapse preference in localStorage, per browser; storage
// that is empty, invalid or blocked means expanded.

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('sidebar preference storage', () => {
  it('is expanded by default', () => {
    expect(readSidebarCollapsed()).toBe(false);
  });

  it('restores collapsed and expanded', () => {
    writeSidebarCollapsed(true);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('collapsed');
    expect(readSidebarCollapsed()).toBe(true);
    writeSidebarCollapsed(false);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('expanded');
    expect(readSidebarCollapsed()).toBe(false);
  });

  it.each(['', 'Collapsed', 'true', 'rail'])(
    'reads the invalid value %j as expanded',
    (value) => {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, value);
      expect(readSidebarCollapsed()).toBe(false);
    },
  );

  it('reads a throwing getItem as expanded', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    expect(readSidebarCollapsed()).toBe(false);
  });

  it('ignores a throwing setItem', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'QuotaExceededError');
    });
    expect(() => writeSidebarCollapsed(true)).not.toThrow();
  });
});
