import {
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AttendanceStatus } from './api';

// An attendance status as an icon with its text (#76 G1: never colour
// alone). `null` reads "Not recorded".

export const STATUS_ICONS: Record<AttendanceStatus, LucideIcon> = {
  present: CircleCheck,
  late: Clock,
  absent: CircleX,
};

export function StatusLabel({ status }: { status: AttendanceStatus | null }) {
  const { t } = useTranslation();
  const Icon = status ? STATUS_ICONS[status] : CircleDashed;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      {status
        ? t(`attendance.statuses.${status}`)
        : t('attendance.notRecorded')}
    </span>
  );
}
