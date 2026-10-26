import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { BADGE_CLASS } from '@/features/academics/styles';
import { ProgressMeter } from '@/features/learning-plans/ProgressMeter';
import { useFormatters } from '@/lib/formatters';
import type { StudentProgress } from './api';

// "Learning plans" (#322 C2.3; not Recharts): a summary line, then one row
// per listed plan (at most 20, the API's order) with the title, the
// subject, a status badge (text), #310's ProgressMeter ("{done} of {total}
// milestones done · {percent} %", the API's whole-number percent as sent),
// the overdue milestones and the target date. The rows are already text,
// so there's no table toggle. No plans: a text empty state.

export function PlanProgressList({ progress }: { progress: StudentProgress }) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const { plans } = progress;
  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('progress.plans.heading')}
      </h2>
      {plans.items.length === 0 ? (
        <p className="text-sm">{t('progress.plans.empty')}</p>
      ) : (
        <>
          <p className="text-sm break-words">
            {t('progress.plans.summary', {
              active: plans.active,
              completed: plans.completed,
              done: plans.objectives_complete,
              total: plans.objectives_total,
            })}
          </p>
          <ul className="flex min-w-0 flex-col gap-3">
            {plans.items.map((plan) => (
              <li
                key={plan.id}
                className="flex min-w-0 flex-col gap-1.5 border-t border-border pt-3 first:border-t-0 first:pt-0"
              >
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <h3 className="min-w-0 text-base font-medium break-words">
                    {plan.title}
                  </h3>
                  <span className={BADGE_CLASS}>
                    {t(`progress.plans.statuses.${plan.status}`)}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground break-words">
                  {plan.subject.name}
                </p>
                <ProgressMeter
                  progress={{
                    done: plan.milestones_done,
                    total: plan.milestones_total,
                    percent: plan.progress_percent,
                  }}
                />
                {plan.milestones_overdue > 0 ? (
                  <p className="text-sm font-medium">
                    {t('progress.plans.overdue', {
                      count: plan.milestones_overdue,
                    })}
                  </p>
                ) : null}
                <p className="text-sm">
                  {plan.target_on
                    ? t('progress.plans.target', {
                        date: formatLocalDate(plan.target_on),
                      })
                    : t('progress.plans.noTarget')}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
