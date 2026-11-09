// The sidebar's collapse preference (#546 B5), kept per browser like the
// theme (theme/theme.ts) and the language (i18n/language.ts). It is not saved
// to the account and needs no API call. Kept free of React so the UI store
// can read it when it is created.

export type SidebarState = 'expanded' | 'collapsed';

export const SIDEBAR_STORAGE_KEY = 'classora.sidebar';

/** Whether the stored choice is `collapsed`; anything else, or storage that
 * throws, means expanded. */
export function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === 'collapsed';
  } catch {
    return false;
  }
}

/** Saves the choice; blocked storage only means it is not kept. */
export function writeSidebarCollapsed(collapsed: boolean): void {
  const value: SidebarState = collapsed ? 'collapsed' : 'expanded';
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, value);
  } catch {
    // Storage is unavailable (for example blocked by the browser).
  }
}
