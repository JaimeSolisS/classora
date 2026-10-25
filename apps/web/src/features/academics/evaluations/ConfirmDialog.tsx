import { useState, type ReactNode } from 'react';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

// A confirmation for the evaluation actions (#308 C5, C7): one request per
// confirm, the buttons disabled while it runs. The page moves focus once it
// closes (the trigger on Cancel or Escape, the outcome after a confirm), so
// the dialog's own return of focus is turned off.

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  busyLabel: string;
  cancelLabel: string;
  destructive?: boolean;
  children?: ReactNode;
  /** Resolves when done; the page closes the dialog. */
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  busyLabel,
  cancelLabel,
  destructive = false,
  children,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) {
          onCancel();
        }
      }}
    >
      {open ? (
        <DialogContent
          className="sm:max-w-lg"
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {children}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onCancel}
            >
              {cancelLabel}
            </Button>
            <Button
              type="button"
              variant={destructive ? 'destructive' : 'default'}
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void onConfirm().finally(() => setBusy(false));
              }}
            >
              {busy ? <Spinner label={busyLabel} showLabel /> : confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
