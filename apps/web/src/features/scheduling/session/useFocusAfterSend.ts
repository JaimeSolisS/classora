import { useEffect, useRef } from 'react';

/**
 * #301b QA D1: the fields are disabled while a send is pending, and a
 * disabled control can't take focus. After a failed send the dialog asks
 * for a focus move through the returned function; it runs once `busy` is
 * false again and the fields are enabled (after that render commits).
 */
export function useFocusAfterSend(busy: boolean): (move: () => void) => void {
  const pending = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!busy && pending.current) {
      const move = pending.current;
      pending.current = null;
      move();
    }
  }, [busy]);
  return (move) => {
    pending.current = move;
  };
}
