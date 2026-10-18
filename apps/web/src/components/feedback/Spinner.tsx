import { LoaderCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SpinnerProps {
  /** Text for the spinner. Without it the spinner is decorative. */
  label?: string;
  /** Shows the label next to the icon; otherwise it is screen-reader only. */
  showLabel?: boolean;
  className?: string;
}

// Inline loading indicator for a control or a short wait (a button that is
// submitting). For a region whose content is loading, use LoadingState.
export function Spinner({ label, showLabel = false, className }: SpinnerProps) {
  return (
    <span
      data-slot="spinner"
      aria-hidden={label ? undefined : true}
      className={cn('inline-flex items-center gap-2', className)}
    >
      <LoaderCircle
        aria-hidden="true"
        className="size-4 shrink-0 animate-spin motion-reduce:animate-none"
      />
      {label ? (
        <span className={showLabel ? undefined : 'sr-only'}>{label}</span>
      ) : null}
    </span>
  );
}
