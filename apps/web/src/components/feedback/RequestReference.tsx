import { Trans, useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

interface RequestReferenceProps {
  requestId: string;
  className?: string;
}

// "Reference: <id>": the request ID in a selectable monospace element, so the
// user can quote it to support (_docs/api.md "Request IDs").
export function RequestReference({
  requestId,
  className,
}: RequestReferenceProps) {
  const { t } = useTranslation();
  return (
    <p className={cn('text-sm break-all text-muted-foreground', className)}>
      <Trans
        t={t}
        i18nKey="feedback.reference"
        values={{ requestId }}
        components={{ id: <code className="font-mono select-all" /> }}
      />
    </p>
  );
}
