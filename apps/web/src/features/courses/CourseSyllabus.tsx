import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { components } from '@classora/api-client';

// A course syllabus, read-only (#64 G5, reused by the cohort page #243 D2):
// the modules in order with their materials and required flag, the
// assessments with their module and minimum score, and the completion
// criteria. The template view passes the live template; a cohort passes
// its snapshot (`CohortDetail.syllabus`), never the live template.

type Module = components['schemas']['CourseTemplateModule'];
type Assessment = components['schemas']['CourseTemplateAssessment'];
type Completion = components['schemas']['CourseCompletion'];

export interface CourseSyllabusProps {
  modules: readonly Module[];
  assessments: readonly Assessment[];
  completion: Completion;
  /** The names of the subjects a module can name (unknown ones are left out). */
  subjectName: ReadonlyMap<string, string>;
  /** The level of the section headings (2 on its own page, 3 inside one). */
  headingLevel?: 2 | 3;
}

export function CourseSyllabus({
  modules,
  assessments,
  completion,
  subjectName,
  headingLevel = 2,
}: CourseSyllabusProps) {
  const { t } = useTranslation();
  const id = useId();
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  const Item = headingLevel === 2 ? 'h3' : 'h4';
  const headingClass =
    headingLevel === 2 ? 'text-xl font-semibold' : 'text-lg font-semibold';
  const moduleTitle = new Map(
    modules.map((module) => [module.id, module.title] as const),
  );
  const required = <T extends { id: string; title: string }>(
    ids: readonly string[],
    items: readonly T[],
  ) =>
    ids.length === 0
      ? t('courses.templates.noneRequired')
      : items
          .filter((item) => ids.includes(item.id))
          .map((item) => item.title)
          .join(', ');

  return (
    <>
      <section
        className="flex flex-col gap-3"
        aria-labelledby={`${id}-modules`}
      >
        <Heading id={`${id}-modules`} className={headingClass}>
          {t('courses.templates.modules')}
        </Heading>
        <ol className="flex flex-col gap-3">
          {modules.map((module) => (
            <li
              key={module.id}
              className="flex flex-col gap-1 rounded-md border border-border p-4 text-sm"
            >
              <Item className="text-base font-semibold break-words">
                {t('courses.templates.moduleHeading', {
                  number: module.position,
                })}
                {': '}
                {module.title}
              </Item>
              <p>
                {[
                  module.is_required
                    ? t('courses.templates.required')
                    : t('courses.templates.optional'),
                  module.subject_id
                    ? (subjectName.get(module.subject_id) ?? null)
                    : null,
                  module.planned_sessions === null
                    ? null
                    : t('courses.templates.plannedValue', {
                        count: module.planned_sessions,
                      }),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {module.description ? (
                <p className="break-words whitespace-pre-line">
                  {module.description}
                </p>
              ) : null}
              {module.materials ? (
                <p className="break-words whitespace-pre-line">
                  <span className="font-medium">
                    {t('courses.templates.materials')}:{' '}
                  </span>
                  {module.materials}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      </section>

      <section
        className="flex flex-col gap-3"
        aria-labelledby={`${id}-assessments`}
      >
        <Heading id={`${id}-assessments`} className={headingClass}>
          {t('courses.templates.assessments')}
        </Heading>
        {assessments.length === 0 ? (
          <p className="text-sm">{t('courses.templates.noAssessments')}</p>
        ) : (
          <ol className="flex flex-col gap-2 text-sm">
            {assessments.map((assessment) => (
              <li key={assessment.id} className="break-words">
                <span className="font-medium">{assessment.title}</span>
                {' · '}
                {[
                  assessment.module_id === null
                    ? t('courses.templates.wholeCourse')
                    : (moduleTitle.get(assessment.module_id) ?? ''),
                  assessment.is_required
                    ? t('courses.templates.assessmentRequired')
                    : t('courses.templates.optional'),
                  assessment.min_score_percent === null
                    ? null
                    : t('courses.templates.minScoreValue', {
                        value: assessment.min_score_percent,
                      }),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section
        className="flex flex-col gap-3"
        aria-labelledby={`${id}-completion`}
      >
        <Heading id={`${id}-completion`} className={headingClass}>
          {t('courses.templates.completion')}
        </Heading>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">
            {t('courses.templates.minAttendance')}
          </dt>
          <dd>
            {completion.min_attendance_percent === null
              ? t('courses.templates.noAttendance')
              : `${completion.min_attendance_percent} %`}
          </dd>
          <dt className="font-medium">
            {t('courses.templates.requiredModules')}
          </dt>
          <dd>{required(completion.required_module_ids, modules)}</dd>
          <dt className="font-medium">
            {t('courses.templates.requiredAssessments')}
          </dt>
          <dd>{required(completion.required_assessment_ids, assessments)}</dd>
        </dl>
      </section>
    </>
  );
}
