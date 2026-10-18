import { useTranslation } from 'react-i18next';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export type LoadingLayout = 'page' | 'table';

interface LoadingStateProps {
  /** `page`: a heading and text lines. `table`: a header row and rows. */
  layout?: LoadingLayout;
  /** Number of text lines or table rows. */
  rows?: number;
  /** Screen-reader message; defaults to the translated "Loading…". */
  label?: string;
  className?: string;
}

// A region whose content is loading: busy, with a visually hidden message,
// and skeleton shapes hidden from assistive technology (the Skeleton rule in
// _docs/design-system.md).
export function LoadingState({
  layout = 'page',
  rows = 4,
  label,
  className,
}: LoadingStateProps) {
  const { t } = useTranslation();
  const lines = Array.from({ length: rows }, (_, index) => index);
  return (
    <div
      data-slot="loading-state"
      aria-busy="true"
      className={cn('flex w-full flex-col gap-3', className)}
    >
      <span className="sr-only">{label ?? t('feedback.loading')}</span>
      {layout === 'page' ? (
        <>
          <Skeleton className="h-8 w-1/2 max-w-xs" />
          {lines.map((line) => (
            <Skeleton
              key={line}
              className={cn('h-4', line === rows - 1 ? 'w-2/3' : 'w-full')}
            />
          ))}
        </>
      ) : (
        <>
          <Skeleton className="h-9 w-full" />
          {lines.map((line) => (
            <Skeleton key={line} className="h-7 w-full" />
          ))}
        </>
      )}
    </div>
  );
}
