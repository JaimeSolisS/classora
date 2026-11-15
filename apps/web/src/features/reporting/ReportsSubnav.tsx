import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { FINANCE_REPORT_ACCESS, KPI_PAGE_ACCESS } from './access';

// The reports area's sub-navigation (M8-09b E1, A3; the SettingsTabs
// pattern): "KPIs" and "Financial report", each shown only to users who may
// open its page, the current one marked aria-current="page" by NavLink.
// With a single visible link there is nothing to switch to, so the bar
// doesn't render and the page's h1 names it.

const PAGES = [
  {
    to: '/reports/kpis',
    labelKey: 'reports.subnav.kpis',
    access: KPI_PAGE_ACCESS,
  },
  {
    to: '/reports/finance',
    labelKey: 'reports.subnav.finance',
    access: FINANCE_REPORT_ACCESS,
  },
] as const;

export function ReportsSubnav() {
  const { t } = useTranslation();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const pages = PAGES.filter((page) => canAccess(user, page.access));
  if (pages.length < 2) return null;
  return (
    <nav aria-label={t('reports.subnav.label')} data-reports-subnav>
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {pages.map((page) => (
          <li key={page.to}>
            <NavLink
              to={page.to}
              className={({ isActive }) =>
                cn(
                  'inline-flex min-h-11 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-9',
                  isActive
                    ? 'border-foreground text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )
              }
            >
              {t(page.labelKey)}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
