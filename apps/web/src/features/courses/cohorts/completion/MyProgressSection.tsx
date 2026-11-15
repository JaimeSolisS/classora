import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { CohortCompletion, StudentCompletion } from './api';
import { ContextLine, CriteriaLine } from './CompletionSummary';
import { CompletionStatusBadge } from './StatusBadge';
import { StudentCompletionDetail } from './StudentCompletionDetail';

// The family's completion on the cohort page (#323 W3): one section per
// student the API returns (a student's own row, a guardian's children),
// headed "My progress" for a student who isn't also a guardian and
// "{name}'s progress" otherwise; the overall status, the context and
// criteria lines and the detail, always expanded. No roster, no table.
// Nothing at all when the API returns no row.

export interface MyProgressSectionProps {
  data: CohortCompletion;
  /** "My progress" (a student-only user) or each student's name. */
  heading: 'mine' | 'named';
}

export function MyProgressSection({ data, heading }: MyProgressSectionProps) {
  if (data.students.length === 0) {
    return null;
  }
  return (
    <>
      {data.students.map((student) => (
        <StudentSection
          key={student.student.id}
          data={data}
          student={student}
          heading={heading}
        />
      ))}
    </>
  );
}

function StudentSection({
  data,
  student,
  heading,
}: {
  data: CohortCompletion;
  student: StudentCompletion;
  heading: 'mine' | 'named';
}) {
  const { t } = useTranslation();
  const id = useId();
  const key = 'courses.cohorts.completion';
  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold break-words">
        {heading === 'mine'
          ? t(`${key}.myHeading`)
          : t(`${key}.studentHeading`, {
              name: student.student.display_name,
            })}
      </h2>
      <CompletionStatusBadge status={student.status} />
      <ContextLine data={data} />
      <CriteriaLine data={data} />
      <StudentCompletionDetail student={student} />
    </section>
  );
}
