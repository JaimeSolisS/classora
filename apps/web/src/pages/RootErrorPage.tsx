import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouteError } from 'react-router';
import { Button } from '@/components/ui/button';
import { RequestReference } from '@/components/feedback/RequestReference';
import { errorRequestId, reportError } from '@/app/errors';
import { useDocumentTitle } from '@/app/useDocumentTitle';

// Error boundary of the root route: catches errors in the layout itself, so
// it renders without the layout. Never shows the error message or stack.
export function RootErrorPage() {
  const error = useRouteError();
  const { t } = useTranslation();
  useDocumentTitle(t('appError.title'));
  useEffect(() => {
    reportError(error, { source: 'root' });
  }, [error]);
  const requestId = errorRequestId(error);

  return (
    <main
      id="main"
      tabIndex={-1}
      className="flex min-h-svh flex-col items-start gap-4 p-4 outline-none"
    >
      <h1 className="text-2xl font-semibold">{t('appError.title')}</h1>
      <p className="text-muted-foreground">{t('appError.message')}</p>
      {requestId ? <RequestReference requestId={requestId} /> : null}
      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline">
          <a href="/">{t('routeError.home')}</a>
        </Button>
        <Button type="button" onClick={() => window.location.reload()}>
          {t('appError.reload')}
        </Button>
      </div>
    </main>
  );
}
