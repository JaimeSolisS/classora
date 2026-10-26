import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { decimalText } from '@/lib/decimal';
import type { StudentProgress } from './api';

// The five stat tiles (#322 C1): text first, never colour alone; one column
// at 320 and 390 px, a grid from md. Percentages and averages are the API's
// decimal text (decimalText); a null one shows "No data yet" and the counts
// still show. Only integer counts are added on the client (attended =
// present + late, recorded = present + late + absent); no rate is computed.

function Tile({
  title,
  value,
  lines,
  caption,
}: {
  title: string;
  value: string;
  lines: readonly (string | null)[];
  caption?: string;
}) {
  return (
    <li className="min-w-0">
      <article className="flex h-full min-w-0 flex-col gap-1 rounded-lg border border-border bg-card p-4 text-card-foreground">
        <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
        {caption ? (
          <p className="text-xs text-muted-foreground">{caption}</p>
        ) : null}
        <p className="text-2xl font-semibold break-words">{value}</p>
        {lines
          .filter((line): line is string => line !== null)
          .map((line) => (
            <p key={line} className="text-sm break-words">
              {line}
            </p>
          ))}
      </article>
    </li>
  );
}

export function StatTiles({ progress }: { progress: StudentProgress }) {
  const { t } = useTranslation();
  const id = useId();
  const { attendance, grades, homework, plans } = progress;
  const percent = (value: string | null) => {
    const text = decimalText(value);
    return text === null
      ? t('progress.noData')
      : t('progress.percent', { value: text });
  };
  const average = (value: string | null) => {
    const text = decimalText(value);
    return text === null
      ? t('progress.noData')
      : t('progress.tiles.average', { value: text });
  };
  const attended = attendance.present + attendance.late;
  const recorded = attended + attendance.absent;
  return (
    <section aria-labelledby={`${id}-heading`} className="min-w-0">
      {/* The tiles' h3 titles sit under this heading (heading order). */}
      <h2 id={`${id}-heading`} className="sr-only">
        {t('progress.tiles.label')}
      </h2>
      <ul className="grid min-w-0 grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Tile
          title={t('progress.tiles.attendance')}
          value={percent(attendance.attendance_percent)}
          lines={[
            t('progress.tiles.attended', { attended, count: recorded }),
            attendance.not_recorded > 0
              ? t('progress.tiles.notRecorded', {
                  count: attendance.not_recorded,
                })
              : null,
          ]}
        />
        <Tile
          title={t('progress.tiles.evaluations')}
          value={average(grades.evaluation_average_percent)}
          lines={[
            t('progress.tiles.evaluationCount', {
              count: grades.evaluation_count,
            }),
          ]}
        />
        <Tile
          title={t('progress.tiles.homeworkGrades')}
          value={average(grades.assignment_average_percent)}
          lines={[
            t('progress.tiles.gradedCount', { count: grades.assignment_count }),
          ]}
        />
        <Tile
          title={t('progress.tiles.homeworkCompletion')}
          caption={t('progress.tiles.homeworkScope')}
          value={percent(homework.completion_percent)}
          lines={[
            t('progress.tiles.homeworkStates', {
              missing: homework.missing,
              late: homework.late,
              pending: homework.pending,
            }),
          ]}
        />
        <Tile
          title={t('progress.tiles.objectives')}
          value={t('progress.tiles.objectivesComplete', {
            done: plans.objectives_complete,
            count: plans.objectives_total,
          })}
          lines={[
            plans.milestones_overdue > 0
              ? t('progress.tiles.overdue', { count: plans.milestones_overdue })
              : null,
          ]}
        />
      </ul>
    </section>
  );
}
