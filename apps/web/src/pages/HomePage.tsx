import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, apiCall } from '@/app/api';
import { APP_NAME, useDocumentTitle } from '@/app/useDocumentTitle';

// Placeholder home page: shows whether the API is live. Feature tasks replace
// the content; the query below is the pattern for hand-written query hooks.
const HEALTH_TIMEOUT_MS = 5000;
const healthLiveKey = ['health', 'live'] as const;

type ApiStatus = 'checking' | 'live' | 'unreachable';

function isLiveBody(body: unknown): boolean {
  return (
    typeof body === 'object' &&
    body !== null &&
    Object.keys(body).length === 1 &&
    (body as { status?: unknown }).status === 'ok'
  );
}

// Aborts after HEALTH_TIMEOUT_MS or when the query is cancelled.
async function fetchLiveness(querySignal: AbortSignal): Promise<unknown> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, HEALTH_TIMEOUT_MS);
  querySignal.addEventListener('abort', abort, { once: true });
  try {
    return await apiCall(
      () =>
        api.GET('/health/live', {
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        }),
      controller.signal,
    );
  } finally {
    clearTimeout(timer);
    querySignal.removeEventListener('abort', abort);
  }
}

export function HomePage() {
  const { t } = useTranslation();
  useDocumentTitle();
  const health = useQuery({
    queryKey: healthLiveKey,
    queryFn: ({ signal }) => fetchLiveness(signal),
    // Show the status promptly instead of retrying.
    retry: false,
  });
  // The generated types are not runtime validation: check the body too.
  const status: ApiStatus = health.isPending
    ? 'checking'
    : health.isSuccess && isLiveBody(health.data)
      ? 'live'
      : 'unreachable';

  return (
    <div className="flex flex-col items-start gap-4">
      <h1 className="text-2xl font-semibold">{APP_NAME}</h1>
      <p role="status">{t(`apiStatus.${status}`)}</p>
    </div>
  );
}
