import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { cn } from '@/lib/utils';
import { usePreferences } from './usePreferences';

// #36 F5: below the header switchers, the save that failed: the choice still
// applies on this device; the error's message and request reference, and
// "Try again". Nothing when there is no failure.
export function PreferenceSaveAlert({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { failure, retry } = usePreferences();
  if (failure === null) {
    return null;
  }
  return (
    <ErrorAlert
      error={failure.error}
      title={t('preferences.saveFailed')}
      onRetry={retry}
      className={cn('max-w-prose', className)}
    />
  );
}
