import { cn } from '@/lib/utils';

// Decorative placeholder: hidden from assistive technology. The region that
// is loading announces itself with aria-busy and a visually hidden label
// (see _docs/design-system.md).
function Skeleton({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      aria-hidden="true"
      data-slot="skeleton"
      className={cn(
        'animate-pulse rounded-md bg-muted motion-reduce:animate-none',
        className,
      )}
      {...props}
    />
  );
}

export { Skeleton };
