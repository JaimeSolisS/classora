import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router';
import { cn } from '@/lib/utils';

// The catalog pages' tab bar (#38 E1): links (not ARIA tabs), the current
// section marked with aria-current="page" by NavLink. A level's page belongs
// to "Levels and subjects". Every catalog route needs catalog.manage, so the
// bar shows every section. #39 adds "Classrooms".

const SECTIONS = [
  { to: '/catalog/education-levels', labelKey: 'catalog.tabs.levels' },
  { to: '/catalog/periods', labelKey: 'catalog.tabs.periods' },
  { to: '/catalog/closures', labelKey: 'catalog.tabs.closures' },
] as const;

export function CatalogTabs() {
  const { t } = useTranslation();
  return (
    <nav aria-label={t('catalog.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {SECTIONS.map((section) => (
          <li key={section.to}>
            <NavLink
              to={section.to}
              className={({ isActive }) =>
                cn(
                  'inline-flex min-h-9 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring',
                  isActive
                    ? 'border-foreground text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )
              }
            >
              {t(section.labelKey)}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
