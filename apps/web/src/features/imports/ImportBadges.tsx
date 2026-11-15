import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { ImportJobStatus, ImportRowStatus } from './api';

// Status badges of an import and of its rows (M8-05b B4, C2, D1): the word
// carries the meaning; the border style only reinforces it (never colour
// alone, WCAG 1.4.1).

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

const PROBLEM = 'border-destructive bg-background';
const QUIET = 'border-border bg-muted';
const PLAIN = 'border-border bg-background';

const JOB_STYLE: Record<ImportJobStatus, string> = {
  staged: PLAIN,
  committing: 'border-dashed border-border bg-background',
  committed: QUIET,
  failed: PROBLEM,
  discarded: QUIET,
  expired: QUIET,
};

const ROW_STYLE: Record<ImportRowStatus, string> = {
  ready: PLAIN,
  duplicate: 'border-dashed border-border bg-background',
  error: PROBLEM,
  created: QUIET,
  skipped: 'border-dashed border-border bg-muted',
  failed: PROBLEM,
};

export function ImportStatusBadge({ status }: { status: ImportJobStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="import-status-badge"
      className={cn(BADGE, JOB_STYLE[status])}
    >
      {t(`imports.status.${status}`)}
    </span>
  );
}

export function ImportRowStatusBadge({ status }: { status: ImportRowStatus }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="import-row-status-badge"
      className={cn(BADGE, ROW_STYLE[status])}
    >
      {t(`imports.rowStatus.${status}`)}
    </span>
  );
}
