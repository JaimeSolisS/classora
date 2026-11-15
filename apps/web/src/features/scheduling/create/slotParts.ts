import { useFormatters } from '@/lib/formatters';
import { toLocalDate } from '@/lib/time';
import type { SuggestedSlot } from './api';

// The date and times of a suggested slot in the center zone (#50 C1), for
// its button and for the announcement once it is chosen.

export function useSlotParts(timeZone: string): (
  slot: Pick<SuggestedSlot, 'starts_at' | 'ends_at'>,
) => {
  date: string;
  start: string;
  end: string;
} {
  const { formatInstant, formatLocalDate } = useFormatters();
  return (slot) => ({
    date: formatLocalDate(toLocalDate(slot.starts_at, timeZone), 'long'),
    start: formatInstant(slot.starts_at, 'time'),
    end: formatInstant(slot.ends_at, 'time'),
  });
}
