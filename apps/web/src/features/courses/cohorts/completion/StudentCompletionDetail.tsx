import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { decimalText } from '@/lib/decimal';
import { useFormatters } from '@/lib/formatters';
import type { StudentCompletion } from './api';
import { CriterionBadge, OutcomeBadge } from './StatusBadge';

// One student's completion detail (#323 W2), shared by the roster's
// disclosure row and the family section: attendance (the counts and the
// minimum), each assessment in the API's order (required as text, its
// minimum, the outcome, and when evaluated the counted score, the date and
// the attempts) and each required module (its status; "Covered by the
// course" for a module evidenced by the course itself). Lists, never a
// table, so it reflows at 320 px. Decimals are the API's text.

export interface StudentCompletionDetailProps {
  student: StudentCompletion;
  /** The level of the three group headings (3 under the section's h2). */
  headingLevel?: 3 | 4;
}

const LIST = 'flex min-w-0 flex-col gap-1 text-sm';

export function StudentCompletionDetail({
  student,
  headingLevel = 3,
}: StudentCompletionDetailProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const Heading = `h${headingLevel}` as const;
  const { attendance, assessments, modules } = student;
  const key = 'courses.cohorts.completion.detail';

  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-3">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Heading id={`${id}-attendance`} className="font-medium">
            {t(`${key}.attendance`)}
          </Heading>
          <CriterionBadge status={attendance.status} />
        </div>
        <ul aria-labelledby={`${id}-attendance`} className={LIST}>
          <li>
            {t(`${key}.attended`, {
              attended: attendance.attended,
              count: attendance.held,
            })}
          </li>
          <li>{t(`${key}.absent`, { count: attendance.absent })}</li>
          <li>{t(`${key}.remaining`, { count: attendance.remaining })}</li>
          {attendance.awaiting_closeout > 0 ? (
            <li>
              {t('courses.cohorts.completion.awaiting', {
                count: attendance.awaiting_closeout,
              })}
            </li>
          ) : null}
          {attendance.min_percent !== null ? (
            <li>{t(`${key}.minimum`, { percent: attendance.min_percent })}</li>
          ) : null}
        </ul>
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Heading id={`${id}-assessments`} className="font-medium">
            {t(`${key}.assessments`)}
          </Heading>
          <CriterionBadge status={assessments.status} />
        </div>
        {assessments.items.length === 0 ? (
          <p className="text-sm">{t(`${key}.noAssessments`)}</p>
        ) : (
          <ul
            aria-labelledby={`${id}-assessments`}
            className="flex flex-col gap-2"
          >
            {assessments.items.map((item) => {
              const score = decimalText(item.score_percent);
              const evaluated = item.status !== 'not_evaluated';
              return (
                <li
                  key={item.assessment_id}
                  className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-2 text-sm"
                >
                  <span className="font-medium break-words">{item.title}</span>
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <OutcomeBadge status={item.status} />
                    {item.is_required ? (
                      <span>{t(`${key}.required`)}</span>
                    ) : null}
                    {item.min_score_percent !== null ? (
                      <span>
                        {t(`${key}.minimum`, {
                          percent: item.min_score_percent,
                        })}
                      </span>
                    ) : null}
                  </span>
                  {evaluated ? (
                    <span className="flex flex-wrap gap-x-2 gap-y-1 text-muted-foreground">
                      {score !== null ? (
                        <span>{t(`${key}.score`, { percent: score })}</span>
                      ) : null}
                      {item.evaluated_on ? (
                        <span>
                          {t(`${key}.evaluatedOn`, {
                            date: formatLocalDate(item.evaluated_on),
                          })}
                        </span>
                      ) : null}
                      <span>
                        {t(`${key}.attempts`, { count: item.attempts })}
                      </span>
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Heading id={`${id}-modules`} className="font-medium">
            {t(`${key}.modules`)}
          </Heading>
          <CriterionBadge status={modules.status} />
        </div>
        {modules.items.length === 0 ? (
          <p className="text-sm">{t(`${key}.noModules`)}</p>
        ) : (
          <ul aria-labelledby={`${id}-modules`} className="flex flex-col gap-2">
            {modules.items.map((item) => (
              <li
                key={item.module_id}
                className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-2 text-sm"
              >
                <span className="font-medium break-words">{item.title}</span>
                <CriterionBadge status={item.status} />
                {item.basis === 'course_covered' ? (
                  <>
                    <span>{t(`${key}.courseCovered`)}</span>
                    <span className="text-muted-foreground">
                      {t(`${key}.courseCoveredHelp`)}
                    </span>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
