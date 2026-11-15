import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from './ConfirmDialog';

// "Finalize this evaluation?" (#308 C5): who will see it once it is final.
// Confirm sends POST …/finalize with the version.

export function FinalizeDialog({
  open,
  student,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  student: string;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      open={open}
      title={t('academics.evaluations.finalize.title')}
      description={t('academics.evaluations.finalize.description', {
        student,
      })}
      confirmLabel={t('academics.evaluations.finalize.confirm')}
      busyLabel={t('academics.evaluations.finalize.busy')}
      cancelLabel={t('academics.evaluations.cancel')}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
