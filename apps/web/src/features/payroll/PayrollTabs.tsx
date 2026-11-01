import type { ParseKeys } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';
import { cn } from '@/lib/utils';

// The payroll pages' tab bar (#425 A4), on the BillingTabs pattern: links,
// the current section marked with aria-current="page". #107b and #110b add
// their sections here.

interface PayrollSection {
  to: string;
  labelKey: ParseKeys<'common'>;
}

const PAYROLL_SECTIONS: readonly PayrollSection[] = [
  { to: '/payroll/tutors', labelKey: 'payroll.tabs.tutors' },
  { to: '/payroll/settings', labelKey: 'payroll.tabs.settings' },
];

export function PayrollTabs() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  return (
    <nav aria-label={t('payroll.tabs.label')} className="-mb-4">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 border-b border-border">
        {PAYROLL_SECTIONS.map((section) => {
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
