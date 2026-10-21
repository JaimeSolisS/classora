import { zodResolver } from '@hookform/resolvers/zod';
import { useId, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
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
import { FieldError } from '@/features/auth/FieldError';
import { z } from '@/lib/forms';
import type { QualificationItem } from './api';

// "Revoke qualification" (#198 E1): a reviewer (qualification.review)
// withdraws an approved qualification with a required reason (1-1000
// characters once trimmed; React Hook Form + Zod). Without any API call: the
// section passes `onConfirm`, which resolves once the qualification is
// revoked, and stories pass a stub. API errors show in the dialog's alert
// region: qualification_not_approved, and version_conflict with "Reload"
// (`onReload` refetches the list; the dialog then sends the new version).

export const REVOCATION_REASON_MAX_LENGTH = 1000;

type MessageKey =
  | 'tutors.qualifications.revokeReasonRequired'
  | 'tutors.qualifications.revokeReasonTooLong';

const formSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(1, 'tutors.qualifications.revokeReasonRequired')
    .max(
      REVOCATION_REASON_MAX_LENGTH,
      'tutors.qualifications.revokeReasonTooLong',
    ),
});

type FormFields = z.infer<typeof formSchema>;

interface RevokeQualificationDialogProps {
  /** The approved qualification to revoke, or null when closed. */
  item: QualificationItem | null;
  /** Revokes with the trimmed reason; rejects with the error to show. */
  onConfirm: (item: QualificationItem, reason: string) => Promise<void>;
  /** Refetches the qualifications after a version conflict. */
  onReload: () => Promise<void>;
  onClose: () => void;
}

export function RevokeQualificationDialog({
  item,
  onConfirm,
  onReload,
  onClose,
}: RevokeQualificationDialogProps) {
  const sending = useRef(false);
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onClose();
        }
      }}
    >
      {item ? (
        <RevokeContent
          key={item.id}
          item={item}
          onConfirm={onConfirm}
          onReload={onReload}
          onClose={onClose}
          sendingRef={sending}
        />
      ) : null}
    </Dialog>
  );
}

function RevokeContent({
  item,
  onConfirm,
  onReload,
  onClose,
  sendingRef,
}: Omit<RevokeQualificationDialogProps, 'item'> & {
  item: QualificationItem;
  sendingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const id = useId();
  const reasonId = `${id}-reason`;
  const reasonErrorId = `${id}-reason-error`;
  const returnFocus = useRef<Element | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormFields>({
    resolver: zodResolver(formSchema),
    defaultValues: { reason: '' },
    shouldFocusError: true,
  });

  async function send({ reason }: FormFields) {
    if (sendingRef.current) {
      return;
    }
    sendingRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(item, reason);
    } catch (caught) {
      setError(caught);
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }

  async function reload() {
    setError(null);
    await onReload();
  }

  const reasonMessage = errors.reason?.message
    ? t(errors.reason.message as MessageKey)
    : undefined;
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  return (
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
        <DialogTitle>{t('tutors.qualifications.revokeTitle')}</DialogTitle>
        <DialogDescription>
          {t('tutors.qualifications.revokeDescription', {
            subject: item.subject_name,
            level: item.education_level_name,
          })}
        </DialogDescription>
      </DialogHeader>
      <form
        noValidate
        onSubmit={(event) => void handleSubmit(send)(event)}
        className="flex flex-col gap-4"
      >
        {error ? (
          <ErrorAlert
            error={error}
            onRetry={isConflict ? () => void reload() : undefined}
            retryLabel={
              isConflict ? t('tutors.qualifications.reload') : undefined
            }
          />
        ) : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={reasonId} className="text-sm font-medium">
            {t('tutors.qualifications.revokeReason')}
          </label>
          <textarea
            id={reasonId}
            rows={3}
            maxLength={REVOCATION_REASON_MAX_LENGTH}
            required
            aria-invalid={reasonMessage ? true : undefined}
            aria-describedby={reasonMessage ? reasonErrorId : undefined}
            disabled={busy}
            className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
            {...register('reason')}
          />
          <FieldError id={reasonErrorId} message={reasonMessage} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={onClose}
          >
            {t('tutors.qualifications.close')}
          </Button>
          <Button type="submit" variant="destructive" disabled={busy}>
            {busy ? (
              <Spinner label={t('tutors.qualifications.revoking')} showLabel />
            ) : (
              t('tutors.qualifications.revokeConfirm')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
