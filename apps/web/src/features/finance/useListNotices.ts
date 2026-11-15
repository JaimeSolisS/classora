import { useState } from 'react';

// The page's status line (M7-11b B4, B5, D1; the #458 C1 rule): a stale
// confirmation (the row is gone or changed) says the list reloaded only when
// it did; otherwise it says it couldn't, until a later refetch succeeds.

export type StaleNotice = 'gone' | 'conflict';

export function useListNotices(
  /** The list's query settled without an error. */
  listReloaded: boolean,
  text: (notice: StaleNotice, reloaded: boolean) => string,
) {
  const [announcement, setAnnouncement] = useState('');
  // A gone or changed row whose reload failed: said until a refetch lands.
  const [notReloaded, setNotReloaded] = useState<StaleNotice | null>(null);
  if (notReloaded && listReloaded) {
    // Adjusting state while rendering (#454 D4).
    setNotReloaded(null);
    setAnnouncement(text(notReloaded, true));
  }
  return {
    announcement,
    announce(value: string) {
      setNotReloaded(null);
      setAnnouncement(value);
    },
    stale(notice: StaleNotice, reloaded: boolean) {
      setAnnouncement(text(notice, reloaded));
      setNotReloaded(reloaded ? null : notice);
    },
  };
}
