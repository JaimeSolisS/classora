import { CircleSlash, ShieldCheck } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { toLocalDate } from '@/lib/time';
import type { CohortDetail, CohortEnrollmentItem } from './api';
import { cohortCapacity, enrollmentControls, rosterParts } from './rules';

// The cohort page's "Students" panel (#245 A), for course.view holders
// (the page renders it only when the detail has a `roster`): the capacity
// line ("8 of 12 enrolled", the room's limit when lower, "Full" as text),
// the active enrollments by name (enrolled on in the center zone, by whom,
// "Overridden" and "Inactive student" as text), and with "Show withdrawn"
// (a checkbox; the page keeps it in the URL) the withdrawn ones with their
// reason. schedule.manage holders get "Enroll student" (not once the
// cohort has ended) and "Withdraw" on each active row.

export interface RosterPanelProps {
  cohort: CohortDetail;
  roster: readonly CohortEnrollmentItem[];
  timeZone: string;
  canManage: boolean;
  /** Link each name to its student page (student.view). */
  linkStudents: boolean;
  showWithdrawn: boolean;
  onShowWithdrawnChange: (shown: boolean) => void;
  onEnroll: () => void;
  onWithdraw: (item: CohortEnrollmentItem) => void;
}

const BADGE =
  'inline-flex w-fit items-center gap-1 rounded-md border border-border px-1.5 text-xs font-medium';

export function RosterPanel({
  cohort,
  roster,
  timeZone,
  canManage,
  linkStudents,
  showWithdrawn,
  onShowWithdrawnChange,
  onEnroll,
  onWithdraw,
}: RosterPanelProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const capacity = cohortCapacity(cohort);
  const controls = enrollmentControls(cohort, canManage);
  const { active, withdrawn } = rosterParts(roster);
  const day = (instant: string) =>
    formatLocalDate(toLocalDate(instant, timeZone));

  function student(item: CohortEnrollmentItem) {
    const name = item.student.display_name;
    return (
      <div className="flex flex-wrap items-center gap-2">
        {linkStudents ? (
          <Link
            to={`/students/${encodeURIComponent(item.student.id)}`}
            className={`${TEXT_LINK_CLASS} font-medium break-words`}
          >
            {name}
          </Link>
        ) : (
          <span className="font-medium break-words">{name}</span>
        )}
        {item.student.is_active ? null : (
          <span className="text-sm text-muted-foreground">
            {t('courses.cohorts.enrollment.inactive')}
          </span>
        )}
      </div>
    );
  }

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-4"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('courses.cohorts.enrollment.heading')}
      </h2>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <p>
          {t('courses.cohorts.enrollment.capacity', {
            enrolled: capacity.enrolled,
            max: capacity.max,
          })}
        </p>
        {capacity.roomLimit !== null ? (
          <p className="text-sm text-muted-foreground">
            {t('courses.cohorts.enrollment.roomLimit', {
              capacity: capacity.roomLimit,
            })}
          </p>
        ) : null}
        {capacity.full ? (
          <span className={BADGE}>
            <CircleSlash aria-hidden="true" className="size-3.5 shrink-0" />
            {t('courses.cohorts.enrollment.full')}
          </span>
        ) : null}
      </div>
      {controls.enroll ? (
        <div>
          <Button type="button" onClick={onEnroll}>
            {t('courses.cohorts.enrollment.enroll')}
          </Button>
        </div>
      ) : null}
      <div className="flex flex-col gap-3">
        <h3 id={`${id}-active`} className="font-medium">
          {t('courses.cohorts.enrollment.active', { count: active.length })}
        </h3>
        {active.length === 0 ? (
          <p className="text-sm">{t('courses.cohorts.enrollment.none')}</p>
        ) : (
          <Table aria-labelledby={`${id}-active`}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('courses.cohorts.enrollment.student')}
                </TableHead>
                <TableHead scope="col">
                  {t('courses.cohorts.enrollment.enrolledAt')}
                </TableHead>
                <TableHead scope="col">
                  {t('courses.cohorts.enrollment.enrolledBy')}
                </TableHead>
                {controls.withdraw ? (
                  <TableHead scope="col">
                    <span className="sr-only">
                      {t('courses.cohorts.enrollment.actions')}
                    </span>
                  </TableHead>
                ) : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {active.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="whitespace-normal">
                    <div className="flex flex-wrap items-center gap-2">
                      {student(item)}
                      {item.overridden ? (
                        <span className={BADGE}>
                          <ShieldCheck
                            aria-hidden="true"
                            className="size-3.5 shrink-0"
                          />
                          {t('courses.cohorts.enrollment.overridden')}
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>{day(item.enrolled_at)}</TableCell>
                  <TableCell>{item.enrolled_by.display_name}</TableCell>
                  {controls.withdraw ? (
                    <TableCell>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('courses.cohorts.enrollment.withdrawOf', {
                          name: item.student.display_name,
                        })}
                        onClick={() => onWithdraw(item)}
                      >
                        {t('courses.cohorts.enrollment.withdraw')}
                      </Button>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${id}-show-withdrawn`}
          checked={showWithdrawn}
          onCheckedChange={(checked) => onShowWithdrawnChange(checked === true)}
        />
        <label htmlFor={`${id}-show-withdrawn`} className="text-sm">
          {t('courses.cohorts.enrollment.showWithdrawn')}
        </label>
      </div>
      {showWithdrawn ? (
        <div className="flex flex-col gap-3">
          <h3 id={`${id}-withdrawn`} className="font-medium">
            {t('courses.cohorts.enrollment.withdrawn', {
              count: withdrawn.length,
            })}
          </h3>
          {withdrawn.length === 0 ? (
            <p className="text-sm">
              {t('courses.cohorts.enrollment.noWithdrawn')}
            </p>
          ) : (
            <Table aria-labelledby={`${id}-withdrawn`}>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    {t('courses.cohorts.enrollment.student')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('courses.cohorts.enrollment.withdrawnAt')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('courses.cohorts.enrollment.withdrawnBy')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('courses.cohorts.enrollment.reason')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {withdrawn.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="whitespace-normal">
                      {student(item)}
                    </TableCell>
                    <TableCell>
                      {item.withdrawn_at ? day(item.withdrawn_at) : ''}
                    </TableCell>
                    <TableCell>
                      {item.withdrawn_by?.display_name ?? ''}
                    </TableCell>
                    <TableCell className="break-words whitespace-normal">
                      {item.withdrawal_reason ??
                        t('courses.cohorts.enrollment.noReason')}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      ) : null}
    </section>
  );
}
