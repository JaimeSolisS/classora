import { CircleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { errorMessage, errorRequestId } from '@/app/errors';
import { cn } from '@/lib/utils';
import { RequestReference } from './RequestReference';

interface ErrorAlertProps {
  /** Any thrown value; an ApiError gives the most specific message. */
  error: unknown;
  /** Shows a "Try again" button (for query errors). */
  onRetry?: () => void;
  className?: string;
}

// Shows a failed request: the translated message for its code and, when
// there is one, the request ID to quote to support. Never shows the server's
// title or detail.
export function ErrorAlert({ error, onRetry, className }: ErrorAlertProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const requestId = errorRequestId(error);
  return (
    <div
      role="alert"
      data-slot="error-alert"
      className={cn(
        'flex gap-3 rounded-md border border-destructive bg-background p-4 text-foreground',
        className,
      )}
    >
      <CircleAlert
        aria-hidden="true"
        className="mt-0.5 size-5 shrink-0 text-destructive"
      />
      <div className="flex min-w-0 flex-col items-start gap-2">
        <p className="font-medium text-destructive">
          {errorMessage(error, tErrors)}
        </p>
        {requestId ? <RequestReference requestId={requestId} /> : null}
        {onRetry ? (
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            {t('feedback.retry')}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
