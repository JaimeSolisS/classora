import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router';
import { cn } from '@/lib/utils';

// The billing pages' tab bar (#363 A2), on the CatalogTabs pattern: links
// (not ARIA tabs), the current section marked with aria-current="page" by
// NavLink. An item's page belongs to "Prices". Later billing pages (tuition
// plans, packages, discounts, payments) add their section here without
// changing the layout; a section a user can't open should then be left out
// with canAccess, like the header's items.

const SECTIONS = [
  { to: '/billing/prices', labelKey: 'billing.tabs.prices' },
] as const;

export function BillingTabs() {
  const { t } = useTranslation();
  return (
    <nav aria-label={t('billing.tabs.label')} className="-mb-4">
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
