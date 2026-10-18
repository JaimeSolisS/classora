import { afterEach, describe, expect, it } from 'vitest';
import { initialUiState, resetUiStore, useUiStore } from './ui';

afterEach(() => {
  resetUiStore();
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
