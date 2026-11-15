import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { CourseTemplate } from './api';
import { CourseSyllabus } from './CourseSyllabus';
import { useDuration } from './useDuration';

// The read-only course template (#64 G5): for holders of course.view
// without course.manage (Finance Staff). The general fields, the modules
// and assessments in order, and the completion criteria, as text.

export function CourseTemplateView({ template }: { template: CourseTemplate }) {
  const { t } = useTranslation();
  const duration = useDuration();
  const id = useId();
  const subjectName = new Map(
    template.subjects.map((subject) => [subject.id, subject.name] as const),
  );

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

      <CourseSyllabus
        modules={template.modules}
        assessments={template.assessments}
        completion={template.completion}
        subjectName={subjectName}
      />
    </div>
  );
}
