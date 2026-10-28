import { useTranslation } from 'react-i18next';
import { Navigate, NavLink } from 'react-router';
import type { SessionUser } from '@/features/auth/session';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { billingSectionsFor } from './sections';

// The billing pages' tab bar (#363 A2), on the CatalogTabs pattern: links
// (not ARIA tabs), the current section marked with aria-current="page" by
// NavLink. An item's page belongs to "Prices". A section the user can't
// open (sections.ts, #366 A1) is left out with canAccess, like the
// header's items.

function useSessionUser(): SessionUser | null {
  const session = useSession();
  return session.status === 'signedIn' ? session.user : null;
}

export function BillingTabs() {
  const { t } = useTranslation();
  const sections = billingSectionsFor(useSessionUser());
  return (
    <nav aria-label={t('billing.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {sections.map((section) => (
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

/**
 * `/billing` (#366 A2), where the header's Billing item points: the first
 * section the user may open. The route's guard (`BILLING_ACCESS`) already
 * turned away anyone who can open none.
 */
export function BillingIndexPage() {
  const first = billingSectionsFor(useSessionUser())[0];
  return first ? <Navigate to={first.to} replace /> : null;
}
