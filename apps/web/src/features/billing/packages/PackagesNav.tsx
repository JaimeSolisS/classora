import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router';
import { useCan } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';

// The Packages tab's sub-navigation (#377 A2): "Sold" and, for
// billing.manage holders, "Products". Links (not ARIA tabs), the current
// one marked with aria-current="page" by NavLink.

export function PackagesNav() {
  const { t } = useTranslation();
  const canManage = useCan('billing.manage');
  const links = [
    { to: '/billing/packages', label: t('billing.packages.nav.sold') },
    ...(canManage
      ? [
          {
            to: '/billing/package-products',
            label: t('billing.packages.nav.products'),
          },
        ]
      : []),
  ];
  return (
    <nav aria-label={t('billing.packages.nav.label')}>
      <ul className="flex flex-wrap gap-2">
        {links.map((link) => (
          <li key={link.to}>
            <NavLink
              to={link.to}
              className={({ isActive }) =>
                cn(
                  'inline-flex min-h-9 items-center rounded-md border px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring',
                  isActive
                    ? 'border-foreground bg-background text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )
              }
            >
              {link.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
