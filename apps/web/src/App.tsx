import { createApiClient } from '@classora/api-client';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '@/i18n/LanguageSwitcher';
import { useLanguage } from '@/i18n/use-language';
import { ThemeProvider } from '@/theme/ThemeProvider';
import { ThemeSwitcher } from '@/theme/ThemeSwitcher';
import { useTheme } from '@/theme/use-theme';

// Placeholder page. #13 replaces it with the app shell and moves both
// switchers into the shell header.
const api = createApiClient();
const TIMEOUT_MS = 5000;

type ApiStatus = 'checking' | 'live' | 'unreachable';

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

function AppLanguageSwitcher() {
  const { language, setLanguage } = useLanguage();
  return <LanguageSwitcher value={language} onValueChange={setLanguage} />;
}

export default function App() {
  const { t } = useTranslation();
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
        <p role="status">{t(`apiStatus.${status}`)}</p>
        <div className="flex flex-wrap gap-4">
          <AppThemeSwitcher />
          <AppLanguageSwitcher />
        </div>
      </main>
    </ThemeProvider>
  );
}
