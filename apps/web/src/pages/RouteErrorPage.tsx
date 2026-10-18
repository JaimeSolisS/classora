import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useRouteError } from 'react-router';
import { Button } from '@/components/ui/button';
import { RequestReference } from '@/components/feedback/RequestReference';
import { errorRequestId, reportError } from '@/app/errors';
import { useDocumentTitle } from '@/app/useDocumentTitle';

// Error boundary of the app pages (the guard route's errorElement): it
// renders inside the layout, so the header, navigation and switchers stay
// usable. Shows a generic message and the request ID of an ApiError, never
// the error message or stack.
export function RouteErrorPage() {
  const error = useRouteError();
  const { t } = useTranslation();
  useDocumentTitle(t('routeError.title'));
  useEffect(() => {
    reportError(error, { source: 'route' });
  }, [error]);
  const requestId = errorRequestId(error);

  return (
    <div className="flex flex-col items-start gap-4">
      <h1 className="text-2xl font-semibold">{t('routeError.title')}</h1>
      <p className="text-muted-foreground">{t('routeError.message')}</p>
      {requestId ? <RequestReference requestId={requestId} /> : null}
      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline">
          <Link to="/">{t('routeError.home')}</Link>
        </Button>
        <Button type="button" onClick={() => window.location.reload()}>
          {t('routeError.reload')}
        </Button>
      </div>
    </div>
  );
}
