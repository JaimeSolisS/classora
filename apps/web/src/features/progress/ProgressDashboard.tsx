import type { StudentProgress } from './api';
import { AttendanceChart } from './charts/AttendanceChart';
import { GradeTrendChart } from './charts/GradeTrendChart';
import { PlanProgressList } from './PlanProgressList';
import { StatTiles } from './StatTiles';

// The progress summary (#322 C1–C3): the five stat tiles, the attendance
// and grade trend charts (each with a text summary and a data table) and
// the learning plans. Only the two lazy progress pages render it, so
// Recharts stays in their route chunk (A3).

export function ProgressDashboard({
  progress,
  chartWidth,
}: {
  progress: StudentProgress;
  /** A fixed chart width (tests). */
  chartWidth?: number;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <StatTiles progress={progress} />
      <div className="grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-2">
        <AttendanceChart progress={progress} width={chartWidth} />
        <GradeTrendChart progress={progress} width={chartWidth} />
      </div>
      <PlanProgressList progress={progress} />
    </div>
  );
}
