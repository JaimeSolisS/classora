import type { ParseKeys } from 'i18next';
import type { AccessRequirement } from '@/features/auth/access';
import { EXPENSE_CONFIGURE_ACCESS } from './access';

// The expense pages' sections (M7-11b A3), in tab order. Each names what its
// route requires, so the tab bar and `/expenses` show only the ones the user
// can open: #115b puts the expense list first (expense.view), and
// Administrators keep Categories and Vendors only.

export interface ExpensesSection {
  to: string;
  labelKey: ParseKeys<'common'>;
  requires: AccessRequirement;
}

export const EXPENSES_SECTIONS: readonly ExpensesSection[] = [
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
