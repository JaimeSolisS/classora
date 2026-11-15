import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { Blocker } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

// "Leave without saving?" (#308 B2, C9; the #77 leave pattern): shown while
// a navigation is blocked because the form has unsaved changes. "Stay"
// (or Escape) cancels the navigation and returns focus to what had it (the
// link that was followed); "Leave" goes on.

export function LeaveDialog({ blocker }: { blocker: Blocker }) {
  const { t } = useTranslation();
  const previous = useRef<HTMLElement | null>(null);
  return (
    <Dialog
      open={blocker.state === 'blocked'}
      onOpenChange={(open) => {
        if (!open && blocker.state === 'blocked') {
          blocker.reset();
        }
      }}
    >
      <DialogContent
        onOpenAutoFocus={() => {
          // Still the element that had focus before the dialog opened.
          const active = document.activeElement;
          previous.current = active instanceof HTMLElement ? active : null;
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const target = previous.current;
          previous.current = null;
          if (target?.isConnected) {
            target.focus();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>{t('academics.leave.title')}</DialogTitle>
          <DialogDescription>
            {t('academics.leave.description')}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => blocker.reset?.()}
          >
            {t('academics.leave.stay')}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => blocker.proceed?.()}
          >
            {t('academics.leave.leave')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
