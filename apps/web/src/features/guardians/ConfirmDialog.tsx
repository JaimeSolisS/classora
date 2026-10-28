import { useRef, useState, type ReactNode } from 'react';
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

// A confirmation dialog (#34 I3, I4): deactivating a guardian and unlinking
// a guardian from a student. Focus is trapped while it is open and returns
// to the button that opened it. The confirm button shows a spinner while
// `onConfirm` runs; the caller closes the dialog.

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  /** An alert shown above the buttons (a refused confirm, #363 D4). */
  alert?: ReactNode;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  destructive = false,
  alert,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const returnFocus = useRef<Element | null>(null);

  async function confirm() {
    if (sending.current) {
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {open ? (
        <DialogContent
          onOpenAutoFocus={() => {
            returnFocus.current = document.activeElement;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus.current;
            if (target instanceof HTMLElement && target.isConnected) {
              target.focus();
            }
          }}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{body}</DialogDescription>
          </DialogHeader>
          {alert}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onCancel}
            >
              {t('guardians.cancel')}
            </Button>
            <Button
              type="button"
              variant={destructive ? 'destructive' : 'default'}
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? (
                <Spinner label={t('guardians.saving')} showLabel />
              ) : (
                confirmLabel
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
