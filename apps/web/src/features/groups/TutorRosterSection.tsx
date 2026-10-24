import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { components } from '@classora/api-client';

// "Students" for the tutor of a group or a cohort (#246 B1, B2; E2): the
// active enrollments' students by name (the API sorts them), never who
// enrolled them, a withdrawal or the staff roster. The API sends
// `tutor_roster` only to the tutor; families never get the section.

export type TutorRosterItem = components['schemas']['TutorRosterItem'];

export interface TutorRosterSectionProps {
  roster: readonly TutorRosterItem[];
}

export function TutorRosterSection({ roster }: TutorRosterSectionProps) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex min-w-0 flex-col gap-3"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('groups.tutorRoster.heading')}
      </h2>
      {roster.length === 0 ? (
        <p className="text-sm">{t('groups.tutorRoster.none')}</p>
      ) : (
        <ul aria-labelledby={`${id}-heading`} className="flex flex-col gap-1">
          {roster.map((item) => (
            <li key={item.student.id} className="break-words">
              {item.student.display_name}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
