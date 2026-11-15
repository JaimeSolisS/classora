import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { EXPENSES_SECTIONS } from './sections';

// The expense pages' tab bar (M7-11b A3), on the PayrollTabs pattern: links,
// the current section marked with aria-current="page". The bar shows only the
// sections the user can open (sections.ts).

export function ExpensesTabs() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const sections = EXPENSES_SECTIONS.filter((section) =>
    canAccess(user, section.requires),
  );
  return (
    <nav aria-label={t('finance.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {sections.map((section) => {
          const active =
            pathname === section.to || pathname.startsWith(`${section.to}/`);
          return (
            <li key={section.to}>
              <Link
                to={section.to}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'inline-flex min-h-11 items-center rounded-t-md border-b-2 px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-9',
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
