import { create } from 'zustand';

// UI-only state (architecture §8). Rule for every store under src/stores/:
// hold only client UI state. Server data lives in TanStack Query, so no store
// imports the API client, TanStack Query or the app's API module, and none holds
// API responses, Problem bodies or ApiError objects. Not persisted. The theme
// stays in ThemeProvider and the language in the i18n instance.

export interface UiState {
  /** Whether the header navigation is expanded below the `lg` breakpoint. */
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  toggleNav: () => void;
}

export const initialUiState: Pick<UiState, 'navOpen'> = { navOpen: false };

export const useUiStore = create<UiState>()((set) => ({
  ...initialUiState,
  setNavOpen: (navOpen) => set({ navOpen }),
  toggleNav: () => set((state) => ({ navOpen: !state.navOpen })),
}));

/** Restores the initial state (used between tests). */
export function resetUiStore(): void {
  useUiStore.setState(initialUiState);
}
