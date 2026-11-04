import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import {
  createExpenseCategory,
  EXPENSE_CATEGORIES_KEY,
  fetchExpenseCategories,
  updateExpenseCategory,
  type ExpenseCategory,
} from '../api';
import { isGone, isVersionConflict, type CategoryFormValues } from '../forms';
import { useCategoryLabel } from '../labels';
import { useListNotices, type StaleNotice } from '../useListNotices';
import type { SaveResult } from '../useRowSave';
import { CategoriesView, type CategoriesState } from './CategoriesView';
import { CategoryDialog } from './CategoryDialog';

// /expenses/categories (M7-11b B), for expense.configure holders. A write
// refreshes the list. A stale confirmation (the row is gone or changed)
// closes, says so and refreshes, so nobody deactivates a row they haven't
// seen; any other refusal stays open. The #458 config-page rules (D1): the
// notice says the list reloaded only when it did; a failed refetch keeps the
// old rows and says they may be out of date, with a reload; closing the
// dialog after a failed save (a lost answer may have stored it) refreshes
// the list, however it closes. A 403 means the permission was lost since the
// session loaded: the forbidden page (A4).

type Editing = { category: ExpenseCategory | null };

const C = 'finance.categories';

export function CategoriesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t(`${C}.title`));
  const queryClient = useQueryClient();
  const label = useCategoryLabel();
  const [editing, setEditing] = useState<Editing | null>(null);
  const saveFailed = useRef(false);

  const list = useQuery({
    queryKey: EXPENSE_CATEGORIES_KEY,
    queryFn: ({ signal }) => fetchExpenseCategories(signal),
    meta: { expectedStatuses: [403] },
  });
  const notices = useListNotices(
    list.isSuccess && !list.isFetching,
    (notice: StaleNotice, reloaded: boolean) =>
      notice === 'gone'
        ? reloaded
          ? t(`${C}.gone`)
          : t(`${C}.goneNotReloaded`)
        : reloaded
          ? t(`${C}.conflictRefreshed`)
          : t(`${C}.conflictNotReloaded`),
  );

  /** Whether the list reloaded: invalidating never rejects. */
  async function refresh(): Promise<boolean> {
    await queryClient.invalidateQueries({ queryKey: EXPENSE_CATEGORIES_KEY });
    return (
      queryClient.getQueryState(EXPENSE_CATEGORIES_KEY)?.status !== 'error'
    );
  }

  async function save(
    values: CategoryFormValues,
    base: ExpenseCategory | null,
  ): Promise<SaveResult> {
    const name = values.name.trim();
    if (base && name === label(base)) {
      return 'unchanged';
    }
    try {
      await (base
        ? updateExpenseCategory(base.id, { version: base.version, name })
        : createExpenseCategory({ name }));
    } catch (caught) {
      saveFailed.current = true;
      throw caught;
    }
    saveFailed.current = false;
    await refresh();
    return 'saved';
  }

  async function setActive(category: ExpenseCategory, active: boolean) {
    notices.announce('');
    try {
      await updateExpenseCategory(category.id, {
        version: category.version,
        is_active: active,
      });
    } catch (caught) {
      if (isGone(caught) || isVersionConflict(caught)) {
        notices.stale(isGone(caught) ? 'gone' : 'conflict', await refresh());
        return;
      }
      throw caught;
    }
    await refresh();
    notices.announce(active ? t(`${C}.reactivated`) : t(`${C}.deactivated`));
  }

  if (list.error instanceof ApiError && list.error.status === 403) {
    return <ForbiddenPage />;
  }

  const categories = list.data;
  const state: CategoriesState = categories
    ? {
        status: 'ready',
        categories,
        refreshError: list.isError ? list.error : null,
        onRefresh: () => void list.refetch(),
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  // The live row, so a colleague's version reaches an open dialog; the
  // snapshot if it went missing, so the dialog never closes by itself.
  const edited = editing?.category
    ? (categories?.find((row) => row.id === editing.category?.id) ??
      editing.category)
    : null;

  return (
    <>
      <CategoriesView
        state={state}
        onAdd={() => {
          notices.announce('');
          setEditing({ category: null });
        }}
        onRename={(category) => {
          notices.announce('');
          setEditing({ category });
        }}
        onSetActive={setActive}
        announcement={notices.announcement}
      />
      {editing ? (
        <CategoryDialog
          key={editing.category?.id ?? 'new'}
          open
          category={edited}
          onSubmit={save}
          onReload={refresh}
          onClose={(saved) => {
            setEditing(null);
            if (saved) {
              notices.announce(t(`${C}.saved`));
            }
            if (saveFailed.current) {
              saveFailed.current = false;
              void refresh();
            }
          }}
        />
      ) : null}
    </>
  );
}
