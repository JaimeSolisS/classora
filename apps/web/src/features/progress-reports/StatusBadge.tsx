import {
  CircleCheck,
  CircleDashed,
  CircleSlash,
  Clock,
  RotateCcw,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { BADGE_CLASS } from '@/features/academics/styles';
import type { ReportStatus } from './api';
import { statusLabelKey } from './rules';

// A report's status (#315 C1), shared by every working view: a bordered
// text badge with a decorative icon. The word carries the meaning, never
// the colour. Families never see it (their shape has no status).

const ICONS = {
  draft: CircleDashed,
  submitted: Clock,
  changes_requested: RotateCcw,
  approved_for_family: CircleCheck,
  retracted: CircleSlash,
} as const;

export function StatusBadge({ status }: { status: ReportStatus }) {
  const { t } = useTranslation();
  const Icon = ICONS[status];
  return (
    <span className={`${BADGE_CLASS} gap-1`}>
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {t(statusLabelKey(status))}
    </span>
  );
}
