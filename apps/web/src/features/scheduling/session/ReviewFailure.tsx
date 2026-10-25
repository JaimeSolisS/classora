import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { needsReload } from './closeoutReview';

// A failed closeout decision inside a review dialog (#78 G1): when the
// closeout changed elsewhere (version_conflict, closeout_not_submitted,
// closeout_with_tutor) the message comes with Reload; anything else
// (closeout_self_review, a network error, ...) is the shared ErrorAlert.

export const REVIEW_TEXTAREA_CLASS =
  'w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30';

export function ReviewFailure({
  error,
  onReload,
}: {
  error: unknown;
  onReload: () => void;
}) {
  const { t } = useTranslation();
  if (!error) {
    return null;
  }
  return (
    <ErrorAlert
      error={error}
      onRetry={needsReload(error) ? onReload : undefined}
      retryLabel={t('closeout.review.reload')}
    />
  );
}
