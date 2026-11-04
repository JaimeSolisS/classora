import type { ParseKeys } from 'i18next';
import type { AccessRequirement } from '@/features/auth/access';
import { EXPENSE_CONFIGURE_ACCESS, EXPENSE_VIEW_ACCESS } from './access';

// The expense pages' sections (M7-11b A3), in tab order. Each names what its
// route requires, so the tab bar and `/expenses` show only the ones the user
// can open: the expense list comes first (M7-12b A1, expense.view), and
// Administrators keep Categories and Vendors only.

export interface ExpensesSection {
  to: string;
  labelKey: ParseKeys<'common'>;
  requires: AccessRequirement;
}

export const EXPENSES_SECTIONS: readonly ExpensesSection[] = [
  {
    to: '/expenses/records',
    labelKey: 'finance.tabs.records',
    requires: EXPENSE_VIEW_ACCESS,
  },
  {
    to: '/expenses/categories',
    labelKey: 'finance.tabs.categories',
    requires: EXPENSE_CONFIGURE_ACCESS,
  },
  {
    to: '/expenses/vendors',
    labelKey: 'finance.tabs.vendors',
    requires: EXPENSE_CONFIGURE_ACCESS,
  },
];
