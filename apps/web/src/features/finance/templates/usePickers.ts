import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isCode } from '@/features/payroll/periodRules';
import {
  ALL_VENDORS_KEY,
  EXPENSE_CATEGORIES_KEY,
  fetchAllVendors,
  fetchExpenseCategories,
} from '../api';
import type { PickerState } from '../expenses/NewExpenseDialog';
import { RELOAD_PICKERS_AFTER } from './rules';

// The categories and vendors of the template pages (M7-13b B2, D1, D2): every
// row, active and inactive, for the filters; the dialogs offer the active
// ones. A refusal about the category or vendor reloads both (D5).

export function usePickers() {
  const queryClient = useQueryClient();
  const categories = useQuery({
    queryKey: EXPENSE_CATEGORIES_KEY,
    queryFn: ({ signal }) => fetchExpenseCategories(signal),
  });
  const vendors = useQuery({
    queryKey: ALL_VENDORS_KEY,
    queryFn: ({ signal }) => fetchAllVendors(signal),
  });
  const picker = <T>(query: {
    data: T[] | undefined;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
  }): PickerState<T> =>
    query.data
      ? { status: 'ready', items: query.data }
      : query.isError
        ? {
            status: 'error',
            error: query.error,
            onRetry: () => void query.refetch(),
          }
        : { status: 'loading' };
  return {
    categories,
    vendors,
    categoryPicker: picker(categories),
    vendorPicker: picker(vendors),
    /** After a refusal about the category or vendor (D5). */
    async reloadIfNeeded(caught: unknown) {
      if (!isCode(caught, ...RELOAD_PICKERS_AFTER)) return;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: EXPENSE_CATEGORIES_KEY }),
        queryClient.invalidateQueries({ queryKey: ALL_VENDORS_KEY }),
      ]);
    },
  };
}
