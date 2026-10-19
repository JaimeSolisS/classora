import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

// Status and age badges of a student (#33 I3, J3): the word carries the
// meaning, the border only reinforces it (never colour alone, WCAG 1.4.1).

const BADGE =
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

export function StudentStatusBadge({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="student-status-badge"
      className={cn(
        BADGE,
        active ? 'border-border bg-background' : 'border-border bg-muted',
      )}
    >
      {active ? t('students.status.active') : t('students.status.inactive')}
    </span>
  );
}

export function StudentAgeBadge({ minor }: { minor: boolean }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="student-age-badge"
      className={cn(
        BADGE,
        minor ? 'border-dashed border-border bg-background' : 'border-border',
      )}
    >
      {minor ? t('students.badge.minor') : t('students.badge.adult')}
    </span>
  );
}
