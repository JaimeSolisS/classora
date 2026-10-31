import type { ReactNode } from 'react';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useReturnFocus } from '@/features/scheduling/series/hooks';

// The portal's detail dialogs (#388 D4, E3, F2): a full-screen sheet below
// 768 px (I1), a centred dialog above; read-only, one "Close" (44 px).
// Each dialog reads its query live: the page passes the query's state.

export type DetailState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; value: T };

const SHEET =
  'max-h-[90vh] overflow-y-auto sm:max-w-2xl [&>*]:min-w-0 max-md:top-0 max-md:left-0 max-md:h-dvh max-md:max-h-none max-md:w-full max-md:max-w-none max-md:translate-x-0 max-md:translate-y-0 max-md:rounded-none [&>[data-slot=dialog-close]]:size-11';

export function PortalDialog<T>({
  open,
  title,
  description,
  closeLabel,
  state,
  children,
  onClose,
}: {
  open: boolean;
  title: string;
  description: string;
  closeLabel: string;
  state: DetailState<T>;
  children: (value: T) => ReactNode;
  onClose: () => void;
}) {
  const returnFocus = useReturnFocus();
  let content: ReactNode;
  if (state.status === 'loading') {
    content = <LoadingState rows={4} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    content = children(state.value);
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {open ? (
        <DialogContent
          className={SHEET}
          onOpenAutoFocus={returnFocus.onOpenAutoFocus}
          onCloseAutoFocus={returnFocus.onCloseAutoFocus}
        >
          <DialogHeader className="pr-12">
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {content}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="min-h-11"
              onClick={onClose}
            >
              {closeLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

/** A term list inside a dialog. */
export function DetailTerms({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-2">
      {items.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-sm font-medium text-muted-foreground">{term}</dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
