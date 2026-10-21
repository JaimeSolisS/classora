import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router';
import { cn } from '@/lib/utils';

// The Settings area's tab bar (#61 F1, the #38 CatalogTabs pattern): links
// (not ARIA tabs), the current section marked with aria-current="page" by
// NavLink. Every settings route needs settings.manage, so the bar shows
// every section. M8-04 adds its groups here.

const SECTIONS = [
  { to: '/settings', labelKey: 'settings.tabs.general', end: true },
  {
    to: '/settings/scheduling-policies',
    labelKey: 'settings.tabs.schedulingPolicies',
    end: false,
  },
] as const;

export function SettingsTabs() {
  const { t } = useTranslation();
  return (
    <nav aria-label={t('settings.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {SECTIONS.map((section) => (
          <li key={section.to}>
            <NavLink
              to={section.to}
              end={section.end}
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
