import * as React from 'react';
import { cn } from '@/lib/utils';

// A table wider than its container scrolls sideways inside it (#234 QA D2,
// WCAG 2.1.1). Rows with no focusable cell would leave the hidden columns
// out of keyboard reach, so while the container overflows it takes the
// focus (tabIndex 0) and becomes a region named like the table (its
// aria-labelledby or aria-label). It is measured on resize of the container
// or the table; a table that fits keeps no extra tab stop.
function useOverflows(
  container: React.RefObject<HTMLDivElement | null>,
  table: React.RefObject<HTMLTableElement | null>,
) {
  const [overflows, setOverflows] = React.useState(false);
  React.useLayoutEffect(() => {
    const box = container.current;
    if (!box) {
      return;
    }
    const measure = () => setOverflows(box.scrollWidth > box.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    if (table.current) {
      observer.observe(table.current);
    }
    return () => observer.disconnect();
  }, [container, table]);
  return overflows;
}

function Table({ className, ...props }: React.ComponentProps<'table'>) {
  const container = React.useRef<HTMLDivElement>(null);
  const table = React.useRef<HTMLTableElement>(null);
  const overflows = useOverflows(container, table);
  const labelledBy = props['aria-labelledby'];
  const label = props['aria-label'];
  const named = Boolean(labelledBy || label);
  return (
    <div
      ref={container}
      data-slot="table-container"
      className="relative w-full overflow-x-auto rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
      {...(overflows
        ? {
            tabIndex: 0,
            ...(named
              ? {
                  role: 'region',
                  'aria-labelledby': labelledBy,
                  'aria-label': labelledBy ? undefined : label,
                }
              : {}),
          }
        : {})}
    >
      <table
        data-slot="table"
        className={cn('w-full caption-bottom text-sm', className)}
        {...props}
        ref={table}
      />
    </div>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return (
    <thead
      data-slot="table-header"
      className={cn('[&_tr]:border-b', className)}
      {...props}
    />
  );
}

function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return (
    <tbody
      data-slot="table-body"
      className={cn('[&_tr:last-child]:border-0', className)}
      {...props}
    />
  );
}

function TableFooter({ className, ...props }: React.ComponentProps<'tfoot'>) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        'border-t bg-muted/50 font-medium [&>tr]:last:border-b-0',
        className,
      )}
      {...props}
    />
  );
}

function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        'border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted',
        className,
      )}
      {...props}
    />
  );
}

function TableHead({ className, ...props }: React.ComponentProps<'th'>) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        'h-10 px-2 text-left align-middle font-medium whitespace-nowrap text-foreground [&:has([role=checkbox])]:pr-0',
        className,
      )}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: React.ComponentProps<'td'>) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        'p-2 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0',
        className,
      )}
      {...props}
    />
  );
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<'caption'>) {
  return (
    <caption
      data-slot="table-caption"
      className={cn('mt-4 text-sm text-muted-foreground', className)}
      {...props}
    />
  );
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
};
