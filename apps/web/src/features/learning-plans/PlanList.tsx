import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useFormatters } from '@/lib/formatters';
import type { LearningPlanSummary } from './api';
import { ProgressMeter } from './ProgressMeter';
import { PLAN_LIST_MAX } from './rules';
import { StatusBadge } from './StatusBadge';

// A student's learning plans as a list of cards (#310 B3, B6, E2; phone
// first, at most 100 rows), in the API's order (active, then drafts, then
// closed): the title (a link to the plan), the subject, the status badge,
// the dates, the progress, "{n} of {m} objectives complete", "{n} overdue"
// as text, and "Created by {name}". Shared by the staff list and the
// family list; the page gives the link and the empty text.

export type PlanListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly LearningPlanSummary[];
      truncated: boolean;
    };

export function PlanDates({
  startsOn,
  targetOn,
}: {
  startsOn: string;
  targetOn: string | null;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  return (
    <span>
      {targetOn
        ? t('learningPlans.dates', {
            start: formatLocalDate(startsOn),
            target: formatLocalDate(targetOn),
          })
        : t('learningPlans.datesOpen', { start: formatLocalDate(startsOn) })}
    </span>
  );
}

export function OverdueMarker({ count }: { count: number }) {
  const { t } = useTranslation();
  if (count <= 0) {
    return null;
  }
  return (
    <span className="font-medium text-destructive">
      {t('learningPlans.overdueCount', { count })}
    </span>
  );
}

function PlanCard({ plan, to }: { plan: LearningPlanSummary; to: string }) {
  const { t } = useTranslation();
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-4">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <h2 className="min-w-0 text-base font-semibold break-words">
          <Link
            to={to}
            className="underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
          >
            {plan.title}
          </Link>
        </h2>
        <StatusBadge status={plan.status} />
      </div>
      <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span className="break-words">{plan.subject.name}</span>
        <PlanDates startsOn={plan.starts_on} targetOn={plan.target_on} />
      </p>
      <ProgressMeter progress={plan.progress} />
      <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm">
        <span>
          {t('learningPlans.objectivesComplete', {
            done: plan.objectives_complete,
            count: plan.objectives_total,
          })}
        </span>
        <OverdueMarker count={plan.overdue_milestones} />
      </p>
      <p className="text-sm break-words text-muted-foreground">
        {t('learningPlans.createdBy', { name: plan.created_by.display_name })}
      </p>
    </li>
  );
}

export function PlanList({
  state,
  linkTo,
  emptyText,
}: {
  state: PlanListState;
  linkTo: (planId: string) => string;
  emptyText: string;
}) {
  const { t } = useTranslation();
  if (state.status === 'loading') {
    return <LoadingState rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {state.truncated ? (
        <p role="status" className="text-sm">
          {t('learningPlans.truncated', { max: PLAN_LIST_MAX })}
        </p>
      ) : null}
      {state.items.length === 0 ? (
        <p className="text-sm">{emptyText}</p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-3">
          {state.items.map((plan) => (
            <PlanCard key={plan.id} plan={plan} to={linkTo(plan.id)} />
          ))}
        </ul>
      )}
    </div>
  );
}
