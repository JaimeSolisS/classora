import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { CourseTemplate } from './api';
import { useDuration } from './useDuration';

// The read-only course template (#64 G5): for holders of course.view
// without course.manage (Finance Staff). The general fields, the modules
// and assessments in order, and the completion criteria, as text.

export function CourseTemplateView({ template }: { template: CourseTemplate }) {
  const { t } = useTranslation();
  const duration = useDuration();
  const id = useId();
  const moduleTitle = new Map(
    template.modules.map((module) => [module.id, module.title] as const),
  );
  const subjectName = new Map(
    template.subjects.map((subject) => [subject.id, subject.name] as const),
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
    <div className="flex flex-col gap-8">
      <p className="text-sm text-muted-foreground">
        {t('courses.templates.snapshotHelp')}
      </p>
      <section
        className="flex flex-col gap-3"
        aria-labelledby={`${id}-general`}
      >
        <h2 id={`${id}-general`} className="text-xl font-semibold">
          {t('courses.templates.general')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">{t('courses.templates.description')}</dt>
          <dd className="break-words whitespace-pre-line">
            {template.description ?? '—'}
          </dd>
          <dt className="font-medium">{t('courses.templates.subjects')}</dt>
          <dd>
            {template.subjects
              .map((subject) => {
                const label = t('courses.templates.subjectOption', {
                  name: subject.name,
                  level: subject.level_name,
                });
                return subject.is_active
                  ? label
                  : t('courses.templates.inactiveSubject', { name: label });
              })
              .join(', ')}
          </dd>
          <dt className="font-medium">
            {t('courses.templates.columns.duration')}
          </dt>
          <dd>
            {t('courses.templates.duration', {
              count: template.session_count,
              minutes: template.session_minutes,
              total: duration(template.total_minutes),
            })}
          </dd>
        </dl>
      </section>

      <section
        className="flex flex-col gap-3"
        aria-labelledby={`${id}-modules`}
      >
        <h2 id={`${id}-modules`} className="text-xl font-semibold">
          {t('courses.templates.modules')}
        </h2>
        <ol className="flex flex-col gap-3">
          {template.modules.map((module) => (
            <li
              key={module.id}
              className="flex flex-col gap-1 rounded-md border border-border p-4 text-sm"
            >
              <h3 className="text-base font-semibold break-words">
                {t('courses.templates.moduleHeading', {
                  number: module.position,
                })}
                {': '}
                {module.title}
              </h3>
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
        <h2 id={`${id}-assessments`} className="text-xl font-semibold">
          {t('courses.templates.assessments')}
        </h2>
        {template.assessments.length === 0 ? (
          <p className="text-sm">{t('courses.templates.noAssessments')}</p>
        ) : (
          <ol className="flex flex-col gap-2 text-sm">
            {template.assessments.map((assessment) => (
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
        <h2 id={`${id}-completion`} className="text-xl font-semibold">
          {t('courses.templates.completion')}
        </h2>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="font-medium">
            {t('courses.templates.minAttendance')}
          </dt>
          <dd>
            {template.completion.min_attendance_percent === null
              ? t('courses.templates.noAttendance')
              : `${template.completion.min_attendance_percent} %`}
          </dd>
          <dt className="font-medium">
            {t('courses.templates.requiredModules')}
          </dt>
          <dd>
            {required(
              template.completion.required_module_ids,
              template.modules,
            )}
          </dd>
          <dt className="font-medium">
            {t('courses.templates.requiredAssessments')}
          </dt>
          <dd>
            {required(
              template.completion.required_assessment_ids,
              template.assessments,
            )}
          </dd>
        </dl>
      </section>
    </div>
  );
}
