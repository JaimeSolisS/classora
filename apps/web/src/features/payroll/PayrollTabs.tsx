import type { ParseKeys } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';
import { canAccess, type AccessRequirement } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { PAYROLL_ACCESS, PAYROLL_REVIEW_ACCESS } from './access';

// The payroll pages' tab bar (#425 A4), on the BillingTabs pattern: links,
// the current section marked with aria-current="page". Each section names
// what its route requires, and the bar shows only the ones the user can open
// (#110b A2): a delegated Administrator sees Periods alone.

interface PayrollSection {
  to: string;
  labelKey: ParseKeys<'common'>;
  requires: AccessRequirement;
}

const PAYROLL_SECTIONS: readonly PayrollSection[] = [
  {
    to: '/payroll/periods',
    labelKey: 'payroll.tabs.periods',
    requires: PAYROLL_REVIEW_ACCESS,
  },
  {
    to: '/payroll/tutors',
    labelKey: 'payroll.tabs.tutors',
    requires: PAYROLL_ACCESS,
  },
  {
    to: '/payroll/pay-rules',
    labelKey: 'payroll.tabs.payRules',
    requires: PAYROLL_ACCESS,
  },
  {
    to: '/payroll/pay-item-types',
    labelKey: 'payroll.tabs.payItemTypes',
    requires: PAYROLL_ACCESS,
  },
  {
    to: '/payroll/unresolved',
    labelKey: 'payroll.tabs.unresolved',
    requires: PAYROLL_ACCESS,
  },
  {
    to: '/payroll/settings',
    labelKey: 'payroll.tabs.settings',
    requires: PAYROLL_ACCESS,
  },
];

export function PayrollTabs() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const sections = PAYROLL_SECTIONS.filter((section) =>
    canAccess(user, section.requires),
  );
  return (
    <nav aria-label={t('payroll.tabs.label')} className="-mb-4">
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
