import { createApiClient } from '@classora/api-client';
import { useEffect, useState } from 'react';
import { ThemeProvider } from '@/theme/ThemeProvider';
import { ThemeSwitcher } from '@/theme/ThemeSwitcher';
import { useTheme } from '@/theme/use-theme';

// Placeholder page. The English-only strings are a temporary exception until
// M1-15 externalizes them.
const api = createApiClient();
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
    const { data, response } = await api.GET('/health/live', {
      headers: { Accept: 'application/json' },
      signal,
    });
    // The generated types are not runtime validation: check the body too.
    return response.ok && isLiveBody(data) ? 'live' : 'unreachable';
  } catch {
    return 'unreachable';
  }
}

function AppThemeSwitcher() {
  const { choice, setChoice } = useTheme();
  return <ThemeSwitcher value={choice} onValueChange={setChoice} />;
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
    <ThemeProvider>
      <main className="flex flex-col items-start gap-4 p-4">
        <h1 className="text-2xl font-semibold">Classora</h1>
        <p role="status">{STATUS_TEXT[status]}</p>
        <AppThemeSwitcher />
      </main>
    </ThemeProvider>
  );
}
