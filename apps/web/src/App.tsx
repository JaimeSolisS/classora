import { useEffect, useState } from 'react';

// Placeholder page. The plain fetch and English-only strings are a temporary
// exception: M1-12 moves the call to the generated API client and M1-15
// externalizes the strings.
const HEALTH_URL = '/health/live';
const TIMEOUT_MS = 5000;

type ApiStatus = 'checking' | 'live' | 'unreachable';

const STATUS_TEXT: Record<ApiStatus, string> = {
  checking: 'API: checking…',
  live: 'API: live',
  unreachable: 'API: unreachable',
};

function isLiveBody(body: unknown): boolean {
  return (
    typeof body === 'object' &&
    body !== null &&
    Object.keys(body).length === 1 &&
    (body as { status?: unknown }).status === 'ok'
  );
}

async function checkApi(signal: AbortSignal): Promise<ApiStatus> {
  try {
    const response = await fetch(HEALTH_URL, {
      headers: { Accept: 'application/json' },
      signal,
    });
    if (!response.ok) {
      return 'unreachable';
    }
    const body: unknown = await response.json();
    return isLiveBody(body) ? 'live' : 'unreachable';
  } catch {
    return 'unreachable';
  }
}

export default function App() {
  const [status, setStatus] = useState<ApiStatus>('checking');

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    let active = true;

    void checkApi(controller.signal).then((result) => {
      clearTimeout(timer);
      if (active) {
        setStatus(result);
      }
    });

    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, []);

  return (
    <main>
      <h1>Classora</h1>
      <p role="status">{STATUS_TEXT[status]}</p>
    </main>
  );
}
