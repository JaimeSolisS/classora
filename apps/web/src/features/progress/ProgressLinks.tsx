import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useAssignmentWorker } from '@/features/assignments/access';
import {
  myHomeworkPath,
  studentAssignmentsPath,
} from '@/features/assignments/api';
import { useLearningPlanWriter } from '@/features/learning-plans/access';
import { myPlansPath, studentPlansPath } from '@/features/learning-plans/api';
import { useReportWorker } from '@/features/progress-reports/access';
import {
  myReportsPath,
  studentReportsPath,
} from '@/features/progress-reports/api';

// The related pages under the h1 (#322 F1): academic history (#308),
// learning plans (#310), homework (#313) and tutor reports (#315). Families
// keep ?student=; on the staff page each link shows only to the roles its
// page serves (the API decides what each holds).

export function ProgressLinks({
  studentId,
  audience,
}: {
  studentId: string;
  audience: 'family' | 'staff';
}) {
  const { t } = useTranslation();
  const plans = useLearningPlanWriter();
  const homework = useAssignmentWorker();
  const reports = useReportWorker();
  const family = audience === 'family';
  const links = family
    ? [
        {
          to: `/my-history?student=${encodeURIComponent(studentId)}`,
          label: t('progress.links.history'),
        },
        { to: myPlansPath(studentId), label: t('progress.links.plans') },
        { to: myHomeworkPath(studentId), label: t('progress.links.homework') },
        { to: myReportsPath(studentId), label: t('progress.links.reports') },
      ]
    : [
        {
          to: `/students/${encodeURIComponent(studentId)}/history`,
          label: t('progress.links.history'),
        },
        ...(plans
          ? [
              {
                to: studentPlansPath(studentId),
                label: t('progress.links.plans'),
              },
            ]
          : []),
        ...(homework
          ? [
              {
                to: studentAssignmentsPath(studentId),
                label: t('progress.links.homework'),
              },
            ]
          : []),
        ...(reports
          ? [
              {
                to: studentReportsPath(studentId),
                label: t('progress.links.reports'),
              },
            ]
          : []),
      ];
  return (
    <nav aria-label={t('progress.links.label')}>
      <ul className="flex flex-wrap gap-x-4 gap-y-2">
        {links.map((link) => (
          <li key={link.to}>
            <Link to={link.to} className={SMALL_LINK_CLASS}>
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
