import { ChevronDown, ChevronRight } from 'lucide-react';
import { Fragment, useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { decimalText } from '@/lib/decimal';
import type { CohortCompletion, StudentCompletion } from './api';
import { ContextLine, CriteriaLine } from './CompletionSummary';
import { assessmentCount, moduleCount } from './rules';
import { CompletionStatusBadge } from './StatusBadge';
import { StudentCompletionDetail } from './StudentCompletionDetail';

// "Course completion" on the cohort page (#323 W1), for evaluation.manage
// holders and the cohort's tutor: the criteria line, the context line,
// then a real table in its own focusable scroll region (#311; a contained
// table at every width, the roster can reach 500 rows) with one row per
// actively enrolled student in the API's order: a disclosure button with
// the name, the overall status as a text badge, the attendance, and the
// counts of required assessments passed and required modules met (counts
// of the server's statuses). Each row's detail row (one cell spanning the
// table) holds the shared StudentCompletionDetail. Loading and errors stay
// inside the section.

export type CompletionState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; data: CohortCompletion };

export interface CompletionSectionProps {
  state: CompletionState;
  /** Stories: student ids whose detail starts open. */
  initiallyOpen?: readonly string[];
}

const COLUMNS = 5;

export function CompletionSection({
  state,
  initiallyOpen = [],
}: CompletionSectionProps) {
  const { t } = useTranslation();
  const id = useId();
  const headingId = `${id}-heading`;
  const key = 'courses.cohorts.completion';

  let content: ReactNode;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { data } = state;
    content = (
      <>
        <CriteriaLine data={data} />
        <ContextLine data={data} />
        {data.students.length === 0 ? (
          <p className="text-sm">{t(`${key}.empty`)}</p>
        ) : (
          <RosterTable students={data.students} initiallyOpen={initiallyOpen} />
        )}
      </>
    );
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {t(`${key}.heading`)}
      </h2>
      {content}
    </section>
  );
}

function RosterTable({
  students,
  initiallyOpen,
}: {
  students: readonly StudentCompletion[];
  initiallyOpen: readonly string[];
}) {
  const { t } = useTranslation();
  const id = useId();
  const captionId = `${id}-caption`;
  const [open, setOpen] = useState<ReadonlySet<string>>(
    () => new Set(initiallyOpen),
  );
  const key = 'courses.cohorts.completion';

  function toggle(studentId: string) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(studentId)) {
        next.delete(studentId);
      } else {
        next.add(studentId);
      }
      return next;
    });
  }

  return (
    <div
      role="region"
      aria-labelledby={captionId}
      tabIndex={0}
      className="relative min-w-0 overflow-x-auto rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <table className="w-full min-w-[44rem] border-collapse text-left text-sm">
        <caption id={captionId} className="sr-only">
          {t(`${key}.caption`)}
        </caption>
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="py-2 pr-3 font-medium">
              {t(`${key}.columns.student`)}
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              {t(`${key}.columns.status`)}
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              {t(`${key}.columns.attendance`)}
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              {t(`${key}.columns.assessments`)}
            </th>
            <th scope="col" className="py-2 font-medium">
              {t(`${key}.columns.modules`)}
            </th>
          </tr>
        </thead>
        <tbody>
          {students.map((student, index) => {
            const studentId = student.student.id;
            const expanded = open.has(studentId);
            const detailId = `${id}-detail-${index}`;
            const Icon = expanded ? ChevronDown : ChevronRight;
            const name = student.student.display_name;
            return (
              <Fragment key={studentId}>
                <tr
                  className={
                    expanded ? 'align-top' : 'border-b border-border align-top'
                  }
                >
                  <th
                    scope="row"
                    className="max-w-xs py-2 pr-3 text-left font-normal"
                  >
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-controls={detailId}
                      aria-label={t(`${key}.detailsFor`, { name })}
                      onClick={() => toggle(studentId)}
                      className="inline-flex min-h-6 items-start gap-1 rounded-sm text-left font-medium break-words underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring"
                    >
                      <Icon
                        aria-hidden="true"
                        className="mt-0.5 size-4 shrink-0"
                      />
                      <span className="min-w-0 break-words">{name}</span>
                    </button>
                  </th>
                  <td className="py-2 pr-3">
                    <CompletionStatusBadge status={student.status} />
                  </td>
                  <td className="py-2 pr-3">
                    <AttendanceText student={student} />
                  </td>
                  <td className="py-2 pr-3">
                    <CountText
                      count={assessmentCount(student)}
                      template={`${key}.assessmentsCount`}
                    />
                  </td>
                  <td className="py-2">
                    <CountText
                      count={moduleCount(student)}
                      template={`${key}.modulesCount`}
                    />
                  </td>
                </tr>
                <tr
                  id={detailId}
                  hidden={!expanded}
                  className="border-b border-border"
                >
                  <td colSpan={COLUMNS} className="pt-1 pb-4">
                    {expanded ? (
                      <StudentCompletionDetail student={student} />
                    ) : null}
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** "{attended} of {held} ({percent} %) · {n} awaiting closeout". */
function AttendanceText({ student }: { student: StudentCompletion }) {
  const { t } = useTranslation();
  const key = 'courses.cohorts.completion';
  const { attendance } = student;
  const percent = decimalText(attendance.attendance_percent);
  let text = t(`${key}.attendance.ofHeld`, {
    attended: attendance.attended,
    held: attendance.held,
  });
  if (percent !== null) {
    text += ` ${t(`${key}.attendance.percent`, { percent })}`;
  }
  return (
    <span className="flex flex-col gap-0.5">
      <span className="whitespace-nowrap">{text}</span>
      {attendance.awaiting_closeout > 0 ? (
        <span>
          {t(`${key}.awaiting`, { count: attendance.awaiting_closeout })}
        </span>
      ) : null}
      {attendance.status === 'not_required' ? (
        <span>{t(`${key}.criterion.not_required`)}</span>
      ) : null}
    </span>
  );
}

function CountText({
  count,
  template,
}: {
  count: { done: number; required: number };
  template:
    | 'courses.cohorts.completion.assessmentsCount'
    | 'courses.cohorts.completion.modulesCount';
}) {
  const { t } = useTranslation();
  if (count.required === 0) {
    return <>{t('courses.cohorts.completion.noneRequired')}</>;
  }
  return <>{t(template, { done: count.done, required: count.required })}</>;
}
