import {
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleX,
  Clock,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { BADGE_CLASS } from '@/features/academics/styles';
import type { SubmissionState } from './api';

// A homework row's state (#313 C3), shared by every view: a bordered text
// badge with a decorative icon ("Pending", "Missing", "Handed in",
// "Graded"). The word carries the meaning, never the colour. "Late" is a
// separate text marker (LateMarker).

const ICONS = {
  pending: CircleDashed,
  missing: CircleX,
  submitted: CircleDot,
  graded: CircleCheck,
} as const;

export function StateBadge({ state }: { state: SubmissionState }) {
  const { t } = useTranslation();
  const Icon = ICONS[state];
  return (
    <span className={`${BADGE_CLASS} gap-1`}>
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {t(`assignments.states.${state}`)}
    </span>
  );
}

/** "Late" (handed in after the deadline), as text with an icon. */
export function LateMarker({ late }: { late: boolean }) {
  const { t } = useTranslation();
  if (!late) {
    return null;
  }
  return (
    <span className={`${BADGE_CLASS} gap-1`}>
      <Clock aria-hidden="true" className="size-3.5 shrink-0" />
      {t('assignments.late')}
    </span>
  );
}
