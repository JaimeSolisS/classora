import { useState } from 'react';
import { useTranslation } from 'react-i18next';
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

// The confirmation of "Import N rows" and "Discard" (M8-05b C3): an alert
// dialog (role="alertdialog"; a click outside doesn't close it), one request
// per confirm, the buttons disabled while it runs. The page moves focus when
// it closes (the trigger after Cancel or Escape, the summary after a
// confirm), so the dialog's own return of focus is turned off.

interface ConfirmImportDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  busyLabel: string;
  destructive?: boolean;
  /** Resolves when done; the page closes the dialog. */
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

export function ConfirmImportDialog({
  open,
  title,
  description,
  confirmLabel,
  busyLabel,
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmImportDialogProps) {
  const { t } = useTranslation();
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
          role="alertdialog"
          showCloseButton={false}
          onPointerDownOutside={(event) => event.preventDefault()}
          onInteractOutside={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              disabled={busy}
              onClick={onCancel}
            >
              {t('imports.detail.cancel')}
            </Button>
            <Button
              type="button"
              variant={destructive ? 'destructive' : 'default'}
              className="min-h-11 md:min-h-9"
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
