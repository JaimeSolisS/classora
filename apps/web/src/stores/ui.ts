import { create } from 'zustand';
import {
  readSidebarCollapsed,
  writeSidebarCollapsed,
} from '@/app/layout/sidebar';

// UI-only state (architecture §8). Rule for every store under src/stores/:
// hold only client UI state. Server data lives in TanStack Query, so no store
// imports the API client, TanStack Query or the app's API module, and none holds
// API responses, Problem bodies or ApiError objects. Not persisted, except the
// sidebar's collapse preference (#546 B5), which is kept per browser in
// localStorage (app/layout/sidebar.ts). The theme stays in ThemeProvider and
// the language in the i18n instance.

export interface UiState {
  /** Whether the navigation drawer is open below the `lg` breakpoint. */
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  toggleNav: () => void;
  /** Whether the sidebar is an icon rail at `lg` and up (#546 B5). */
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
}

export const initialUiState: Pick<UiState, 'navOpen'> = { navOpen: false };

export const useUiStore = create<UiState>()((set, get) => ({
  ...initialUiState,
  setNavOpen: (navOpen) => set({ navOpen }),
  toggleNav: () => set((state) => ({ navOpen: !state.navOpen })),
  sidebarCollapsed: readSidebarCollapsed(),
  setSidebarCollapsed: (sidebarCollapsed) => {
    writeSidebarCollapsed(sidebarCollapsed);
    set({ sidebarCollapsed });
  },
  toggleSidebar: () => get().setSidebarCollapsed(!get().sidebarCollapsed),
}));

/** Closes the drawer (at sign-out and between tests). The sidebar's collapse
 * preference is the browser's, so it is kept. */
export function resetUiStore(): void {
  useUiStore.setState(initialUiState);
}

/** Re-reads the stored collapse preference (tests: a fresh page load). */
export function reloadSidebarPreference(): void {
  useUiStore.setState({ sidebarCollapsed: readSidebarCollapsed() });
}
