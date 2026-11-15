import { useRef, useState } from 'react';
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
import { ReviewFailure } from './ReviewFailure';

// "Approve" (#78 G1): a confirmation showing the actual vs scheduled
// minutes. The section passes `onConfirm`, which resolves once the dialog
// may close and rejects with the error to show here. Focus returns to the
// trigger when it closes.

export interface ApproveCloseoutDialogProps {
  open: boolean;
  actualMinutes: number;
  scheduledMinutes: number;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories. */
  initialError?: unknown;
}

export function ApproveCloseoutDialog({
  open,
  actualMinutes,
  scheduledMinutes,
  onConfirm,
  onCancel,
  onReload,
  initialError = null,
}: ApproveCloseoutDialogProps) {
  const { t } = useTranslation();
  const sending = useRef(false);
  const returnFocus = useRef<Element | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(initialError);

  async function confirm() {
    if (sending.current) {
      return;
    }
    sending.current = true;
    setBusy(true);
    setFailure(null);
    try {
      await onConfirm();
    } catch (caught) {
      setFailure(caught);
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
          setFailure(null);
          onCancel();
        }
      }}
    >
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
        className="sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle>{t('closeout.review.approveDialog.title')}</DialogTitle>
          <DialogDescription>
            {t('closeout.review.approveDialog.description')}
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm font-medium">
          {t('closeout.review.approveDialog.minutes', {
            actual: actualMinutes,
            scheduled: scheduledMinutes,
          })}
        </p>
        <ReviewFailure error={failure} onReload={onReload} />
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => {
              setFailure(null);
              onCancel();
            }}
          >
            {t('closeout.review.cancel')}
          </Button>
          <Button type="button" disabled={busy} onClick={() => void confirm()}>
            {busy ? (
              <Spinner
                label={t('closeout.review.approveDialog.submitting')}
                showLabel
              />
            ) : (
              t('closeout.review.approveDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
