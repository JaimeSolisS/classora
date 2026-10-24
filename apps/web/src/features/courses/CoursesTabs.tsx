import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';
import { useCan } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';

// The Courses area's tab bar (#243 A1, the SettingsTabs pattern): links
// (not ARIA tabs), the current section marked with aria-current="page".
// "Templates" is /courses and /courses/templates/*; "Cohorts" is
// /courses/cohorts and /courses/cohorts/*. "Templates" needs course.view:
// a tutor, student or guardian (#246 B2, "My courses") sees "Cohorts" only.

const SECTIONS = [
  {
    to: '/courses',
    labelKey: 'courses.tabs.templates',
    staffOnly: true,
    matches: (path: string) =>
      path === '/courses' ||
      path === '/courses/' ||
      path.startsWith('/courses/templates'),
  },
  {
    to: '/courses/cohorts',
    labelKey: 'courses.tabs.cohorts',
    staffOnly: false,
    matches: (path: string) => path.startsWith('/courses/cohorts'),
  },
] as const;

export function CoursesTabs() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const canViewTemplates = useCan('course.view');
  const sections = SECTIONS.filter(
    (section) => canViewTemplates || !section.staffOnly,
  );
  return (
    <nav aria-label={t('courses.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {sections.map((section) => {
          const current = section.matches(pathname);
          return (
            <li key={section.to}>
              <Link
                to={section.to}
                aria-current={current ? 'page' : undefined}
                className={cn(
                  'inline-flex min-h-9 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring',
                  current
                    ? 'border-foreground text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )}
              >
                {t(section.labelKey)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
