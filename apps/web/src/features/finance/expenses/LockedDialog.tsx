import { useRef, type ComponentType } from 'react';
import { Dialog } from '@/components/ui/dialog';
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import type { LockedControl } from './useLockedSubmission';

// The shell of an expense money dialog (M7-12b D6): the body mounts per
// opening (a new opening is a new key), Escape and the close button can't
// close it while a call is in flight, and every close (Cancel, Escape, the
// close button) passes the worst failure of the opening to `onClose`.

export function LockedDialog<P extends object>({
  open,
  onClose,
  body: Body,
  bodyProps,
}: {
  open: boolean;
  /** Closed without a 201; `failed`: how this opening's attempts failed. */
  onClose: (failed: AttemptFailure) => void;
  /** The dialog's content, mounted per opening. */
  body: ComponentType<P & LockedControl>;
  bodyProps: P;
}) {
  const busyRef = useRef(false);
  const failedRef = useRef<AttemptFailure>('none');
  const close = () => {
    const failed = failedRef.current;
    failedRef.current = 'none';
    onClose(failed);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busyRef.current) close();
      }}
    >
      {open ? (
        <Body
          {...bodyProps}
          busyRef={busyRef}
          failedRef={failedRef}
          onCancel={close}
        />
      ) : null}
    </Dialog>
  );
}
