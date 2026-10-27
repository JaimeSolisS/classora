import { useTranslation } from 'react-i18next';
import type { CohortCompletion } from './api';
import { hasNoCriteria, requiredAssessments } from './rules';

// The criteria line and the context line of the completion sections
// (#323 W1, W3), from the snapshot's criteria and the cohort's status. The
// criteria stay compact: the whole syllabus is already on the page.

export function CriteriaLine({ data }: { data: CohortCompletion }) {
  const { t } = useTranslation();
  const key = 'courses.cohorts.completion.criteria';
  const { criteria } = data;
  if (hasNoCriteria(criteria)) {
    return <p className="text-sm break-words">{t(`${key}.none`)}</p>;
  }
  const assessments = requiredAssessments(criteria);
  return (
    <ul className="flex min-w-0 flex-col gap-1 text-sm">
      {criteria.min_attendance_percent !== null ? (
        <li>
          {t(`${key}.attendance`, {
            percent: criteria.min_attendance_percent,
          })}
        </li>
      ) : null}
      {criteria.required_modules.length > 0 ? (
        <li className="break-words">
          {t(`${key}.modules`, {
            titles: criteria.required_modules
              .map((module) => module.title)
              .join(', '),
          })}
        </li>
      ) : null}
      {assessments.length > 0 ? (
        <li className="break-words">
          {t(`${key}.assessments`, {
            items: assessments
              .map((assessment) =>
                assessment.min_score_percent === null
                  ? assessment.title
                  : t(`${key}.assessmentMinimum`, {
                      title: assessment.title,
                      percent: assessment.min_score_percent,
                    }),
              )
              .join(', '),
          })}
        </li>
      ) : null}
    </ul>
  );
}

/** Shown until the cohort has ended (W1). */
export function ContextLine({ data }: { data: CohortCompletion }) {
  const { t } = useTranslation();
  if (data.cohort_status === 'ended') {
    return null;
  }
  return (
    <p className="text-sm text-muted-foreground">
      {t('courses.cohorts.completion.context')}
    </p>
  );
}
