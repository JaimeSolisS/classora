import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { NOTIFICATIONS_QUERY_KEY } from './api';

// Live notifications (#42 E1, E2): one EventSource per signed-in tab on the
// API's Server-Sent Events stream. Events carry only an id, so on
// `notification` or `resync` the notification queries (count and list) are
// invalidated and refetched through the regular API.
//
// The returned state drives the bell's polling fallback:
// - 'open': the stream is connected, the 30 s polling is off;
// - 'connecting': the browser is (re)connecting by itself (it sends
//   Last-Event-ID); polling runs meanwhile;
// - 'closed': the browser gave up (an HTTP error such as 429 or 401);
//   polling runs and a new EventSource is tried every STREAM_RETRY_MS;
// - 'off': no EventSource in this environment; polling only.

export const NOTIFICATION_STREAM_URL = '/api/v1/notifications/stream';

/** How long after a closed stream a new one is tried. */
export const STREAM_RETRY_MS = 60_000;

export type LiveState = 'off' | 'connecting' | 'open' | 'closed';

const supported = () => typeof globalThis.EventSource === 'function';

/**
 * Opens the stream while mounted (the bell mounts it only for a signed-in
 * user who accepted the privacy notice) and closes it on unmount.
 */
export function useLiveNotifications(): LiveState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<LiveState>('connecting');

  useEffect(() => {
    if (!supported()) {
      return;
    }
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const invalidate = () => {
      void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
    };

    const connect = () => {
      retry = undefined;
      const current = new EventSource(NOTIFICATION_STREAM_URL);
      source = current;
      current.addEventListener('open', () => setState('open'));
      current.addEventListener('error', () => {
        if (current.readyState === EventSource.CLOSED) {
          current.close();
          source = null;
          setState('closed');
          retry = setTimeout(() => {
            setState('connecting');
            connect();
          }, STREAM_RETRY_MS);
        } else {
          setState('connecting');
        }
      });
      current.addEventListener('notification', invalidate);
      current.addEventListener('resync', invalidate);
    };

    connect();
    return () => {
      clearTimeout(retry);
      source?.close();
      source = null;
      setState('connecting');
    };
  }, [queryClient]);

  return supported() ? state : 'off';
}
