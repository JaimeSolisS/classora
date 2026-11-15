import type { Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';

// The failure region of the homework pages (#313 B6, C2, C5, E6),
// focusable (tabIndex -1) so focus moves to it after a refused write: the
// message (ours, or the error's own), "Reload" when the page can refetch,
// and an optional link (the form without its class).

export interface FailureProps {
  error: unknown;
  /** Our message instead of the error's (already translated). */
  message?: string;
  onReload?: () => void;
  link?: { to: string; label: string };
  ref?: Ref<HTMLDivElement>;
}

export function AssignmentFailure({
  error,
  message,
  onReload,
  link,
  ref,
}: FailureProps) {
  const { t } = useTranslation();
  return (
    <div
      ref={ref}
      tabIndex={-1}
      className="flex min-w-0 flex-col gap-2 outline-none"
    >
      <ErrorAlert
        error={error}
        message={message}
        retryLabel={onReload ? t('academics.reload') : undefined}
        onRetry={onReload}
      />
      {link ? (
        <Link to={link.to} className={`${SMALL_LINK_CLASS} self-start`}>
          {link.label}
        </Link>
      ) : null}
    </div>
  );
}
