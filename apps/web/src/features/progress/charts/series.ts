import type { ProgressSubject, StudentProgress } from '../api';

// The chart series rules (#322 C2, C3), shared by the charts and their
// tests. No Recharts import here.

/** The attendance stack, bottom to top (the legend's order too). */
export const ATTENDANCE_SERIES = [
  'present',
  'late',
  'absent',
  'not_recorded',
] as const;
export type AttendanceSeries = (typeof ATTENDANCE_SERIES)[number];

/** At most this many subjects are drawn (one colour token each). */
export const MAX_CHART_SUBJECTS = 4;

/** The subjects of grades.by_month, by name (casefold), then id: a subject
 * keeps its colour whatever the months shown. */
export function trendSubjects(progress: StudentProgress): ProgressSubject[] {
  const seen = new Map<string, ProgressSubject>();
  for (const row of progress.grades.by_month) {
    seen.set(row.subject.id, row.subject);
  }
  return [...seen.values()].sort(
    (a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) ||
      a.id.localeCompare(b.id),
  );
}
