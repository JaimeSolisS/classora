import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { LearningPlanProgress } from './api';

// The progress of a plan or an objective (#310 B5), shared by every view: a
// native <progress> (max = total, value = done) labelled by the text next
// to it, "{done} of {total} milestones done · {percent} %", which is always
// shown: the bar never stands alone. The percent is the API's string as
// sent; the client never computes progress. The fill (primary) against the
// track (muted) and the track's border (input) against the page meet 3:1
// (the design-system contrast tests).

export function ProgressMeter({
  progress,
  className,
}: {
  progress: LearningPlanProgress;
  className?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div
      className={cn(
        'flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1',
        className,
      )}
    >
      <progress
        aria-labelledby={id}
        max={Math.max(progress.total, 1)}
        value={progress.done}
        className="h-2.5 w-32 shrink-0 appearance-none overflow-hidden rounded-full border border-input bg-muted [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:bg-primary"
      />
      <span id={id} className="text-sm">
        {t('learningPlans.progress', {
          done: progress.done,
          total: progress.total,
          percent: progress.percent,
          count: progress.total,
        })}
      </span>
    </div>
  );
}
