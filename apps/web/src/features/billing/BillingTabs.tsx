import { useTranslation } from 'react-i18next';
import { Link, Navigate, useLocation } from 'react-router';
import type { SessionUser } from '@/features/auth/session';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { billingSectionsFor } from './sections';

// The billing pages' tab bar (#363 A2), on the CatalogTabs pattern: links
// (not ARIA tabs), the current section marked with aria-current="page"
// (as NavLink would: its path or a path below it). An item's page belongs
// to "Prices". A section the user can't open (sections.ts, #366 A1) is
// left out with canAccess, like the header's items. A section may own
// other paths too (the Packages tab covers /billing/package-products,
// #377 A1), which NavLink can't express.

function useSessionUser(): SessionUser | null {
  const session = useSession();
  return session.status === 'signedIn' ? session.user : null;
}

export function BillingTabs() {
  const { t } = useTranslation();
  const sections = billingSectionsFor(useSessionUser());
  const { pathname } = useLocation();
  const under = (prefix: string) =>
    pathname === prefix || pathname.startsWith(`${prefix}/`);
  return (
    <nav aria-label={t('billing.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {sections.map((section) => {
          const active =
            under(section.to) || (section.alsoActiveFor ?? []).some(under);
          return (
            <li key={section.to}>
              <Link
                to={section.to}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'inline-flex min-h-9 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring',
                  active
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

/**
 * `/billing` (#366 A2), where the header's Billing item points: the first
 * section the user may open. The route's guard (`BILLING_ACCESS`) already
 * turned away anyone who can open none.
 */
export function BillingIndexPage() {
  const first = billingSectionsFor(useSessionUser())[0];
  return first ? <Navigate to={first.to} replace /> : null;
}
