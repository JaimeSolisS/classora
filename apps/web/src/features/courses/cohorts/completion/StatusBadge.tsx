import {
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  Clock,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type {
  AssessmentCompletionItem,
  CompletionStatus,
  CriterionStatus,
} from './api';

// The completion statuses as bordered text badges with an icon (#323 W5):
// the word carries the meaning, never colour alone (WCAG 1.4.1).

const BADGE =
  'inline-flex w-fit items-center gap-1 rounded-md border border-border bg-background px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-foreground';

function Badge({
  icon: Icon,
  text,
  dashed = false,
}: {
  icon: LucideIcon;
  text: string;
  dashed?: boolean;
}) {
  return (
    <span className={cn(BADGE, dashed && 'border-dashed')}>
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {text}
    </span>
  );
}

const OVERALL_ICONS: Record<CompletionStatus, LucideIcon> = {
  in_progress: Clock,
  completed: CircleCheck,
  not_completed: CircleX,
};

/** "In progress", "Completed" or "Not completed". */
export function CompletionStatusBadge({
  status,
}: {
  status: CompletionStatus;
}) {
  const { t } = useTranslation();
  return (
    <Badge
      icon={OVERALL_ICONS[status]}
      text={t(`courses.cohorts.completion.status.${status}`)}
      dashed={status === 'in_progress'}
    />
  );
}

const CRITERION_ICONS: Record<CriterionStatus, LucideIcon> = {
  met: CircleCheck,
  not_met: CircleX,
  pending: Clock,
  not_required: CircleMinus,
};

/** A criterion's (or a module's) "Met", "Not met", "Pending", "Not required". */
export function CriterionBadge({ status }: { status: CriterionStatus }) {
  const { t } = useTranslation();
  return (
    <Badge
      icon={CRITERION_ICONS[status]}
      text={t(`courses.cohorts.completion.criterion.${status}`)}
      dashed={status === 'pending' || status === 'not_required'}
    />
  );
}

type Outcome = AssessmentCompletionItem['status'];

const OUTCOME_ICONS: Record<Outcome, LucideIcon> = {
  passed: CircleCheck,
  failed: CircleX,
  not_evaluated: CircleDashed,
};

/** An assessment's "Passed", "Failed" or "Not evaluated". */
export function OutcomeBadge({ status }: { status: Outcome }) {
  const { t } = useTranslation();
  return (
    <Badge
      icon={OUTCOME_ICONS[status]}
      text={t(`courses.cohorts.completion.outcome.${status}`)}
      dashed={status === 'not_evaluated'}
    />
  );
}
