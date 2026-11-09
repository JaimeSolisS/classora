import { afterEach, describe, expect, it, vi } from 'vitest';
import { SIDEBAR_STORAGE_KEY } from '@/app/layout/sidebar';
import {
  initialUiState,
  reloadSidebarPreference,
  resetUiStore,
  useUiStore,
} from './ui';

afterEach(() => {
  resetUiStore();
  vi.restoreAllMocks();
  window.localStorage.clear();
  reloadSidebarPreference();
});

describe('UI store', () => {
  it('starts with the navigation collapsed', () => {
    expect(useUiStore.getState().navOpen).toBe(false);
    expect(initialUiState).toEqual({ navOpen: false });
  });

  it('toggles and sets the navigation state', () => {
    useUiStore.getState().toggleNav();
    expect(useUiStore.getState().navOpen).toBe(true);
    useUiStore.getState().toggleNav();
    expect(useUiStore.getState().navOpen).toBe(false);
    useUiStore.getState().setNavOpen(true);
    expect(useUiStore.getState().navOpen).toBe(true);
    useUiStore.getState().setNavOpen(true);
    expect(useUiStore.getState().navOpen).toBe(true);
  });

  it('resets to the initial state (1/2: leave it open)', () => {
    useUiStore.getState().setNavOpen(true);
    expect(useUiStore.getState().navOpen).toBe(true);
  });

  it('resets to the initial state (2/2: starts closed again)', () => {
    expect(useUiStore.getState().navOpen).toBe(false);
  });

  it('keeps its actions after a reset', () => {
    resetUiStore();
    useUiStore.getState().toggleNav();
    expect(useUiStore.getState().navOpen).toBe(true);
  });
});

// #546 B5/H4: the sidebar's collapse preference, written through to storage.
describe('UI store: sidebar preference', () => {
  it('starts expanded with nothing stored', () => {
    expect(useUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it('toggles, sets and writes through', () => {
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('collapsed');
    useUiStore.getState().setSidebarCollapsed(false);
    expect(useUiStore.getState().sidebarCollapsed).toBe(false);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('expanded');
  });

  it('restores the stored preference', () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, 'collapsed');
    reloadSidebarPreference();
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
  });

  it('still toggles for the session when storage throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
  });

  it('keeps the preference when the store is reset (sign-out)', () => {
    useUiStore.getState().setSidebarCollapsed(true);
    useUiStore.getState().setNavOpen(true);
    resetUiStore();
    expect(useUiStore.getState().navOpen).toBe(false);
    expect(useUiStore.getState().sidebarCollapsed).toBe(true);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('collapsed');
  });
});
