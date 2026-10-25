import {
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleSlash,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { BADGE_CLASS } from '@/features/academics/styles';
import type { PlanStatus } from './api';

// A plan's status (#310 B4), shared by every view: a bordered text badge
// with an icon. The word carries the meaning, never the colour; the icon is
// decorative.

const ICONS = {
  draft: CircleDashed,
  active: CircleDot,
  completed: CircleCheck,
  discontinued: CircleSlash,
} as const;

export function StatusBadge({ status }: { status: PlanStatus }) {
  const { t } = useTranslation();
  const Icon = ICONS[status];
  return (
    <span className={`${BADGE_CLASS} gap-1`}>
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {t(`learningPlans.status.${status}`)}
    </span>
  );
}
