import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import type { ExpenseCategory } from '../api';
import { DefaultBadge, StateBadge } from '../Badges';
import { ExpensesTabs } from '../ExpensesTabs';
import { inUseCount } from '../forms';
import { useCategoryLabel } from '../labels';

// The categories tab (M7-11b B1, B4), without any query: the label (the
// catalog's while keyed, M7-11b E2), the "Default" badge on keyed rows and the
// state. Active rows first, each group by the displayed label in the viewer's
// language. A table from `md` up and cards below. Deactivating and
// reactivating sit behind a confirmation, which stays open with the refusal
// when `onSetActive` rejects.

export type CategoriesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      categories: readonly ExpenseCategory[];
      /** A refetch failed: the rows may be out of date (#458 C2). */
      refreshError?: unknown;
      onRefresh?: () => void;
    };

export interface CategoriesViewProps {
  state: CategoriesState;
  onAdd: () => void;
  onRename: (category: ExpenseCategory) => void;
  /** Rejects with the error to show in the confirmation. */
  onSetActive: (category: ExpenseCategory, active: boolean) => Promise<void>;
  announcement?: string;
  /** Stories: the category whose confirmation starts open, and its refusal. */
  initialToggle?: ExpenseCategory;
  initialToggleError?: unknown;
}

const NAME_CLASS = '[overflow-wrap:anywhere]';
const C = 'finance.categories';

function Actions({
  category,
  label,
  onRename,
  onToggle,
}: {
  category: ExpenseCategory;
  label: string;
  onRename: (category: ExpenseCategory) => void;
  onToggle: (category: ExpenseCategory) => void;
}) {
  const { t } = useTranslation();
  const name = { name: label };
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11 md:min-h-8"
        aria-label={t(`${C}.renameFor`, name)}
        onClick={() => onRename(category)}
      >
        {t(`${C}.rename`)}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11 md:min-h-8"
        aria-label={
          category.is_active
            ? t(`${C}.deactivateFor`, name)
            : t(`${C}.reactivateFor`, name)
        }
        onClick={() => onToggle(category)}
      >
        {category.is_active ? t(`${C}.deactivate`) : t(`${C}.reactivate`)}
      </Button>
    </div>
  );
}

export function CategoriesView({
  state,
  onAdd,
  onRename,
  onSetActive,
  announcement = '',
  initialToggle,
  initialToggleError = null,
}: CategoriesViewProps) {
  const { t, i18n } = useTranslation();
  const label = useCategoryLabel();
  const [toggling, setToggling] = useState<ExpenseCategory | null>(
    initialToggle ?? null,
  );
  const [toggleError, setToggleError] = useState<unknown>(initialToggleError);
  // M7-13b F1: a row used by live recurring templates says how many.
  const inUseMessage = (error: unknown) => {
    const count = inUseCount(error);
    return count === null ? undefined : t(`${C}.toggle.inUse`, { count });
  };

  function startToggle(category: ExpenseCategory) {
    setToggleError(null);
    setToggling(category);
  }

  async function confirmToggle() {
    if (!toggling) {
      return;
    }
    setToggleError(null);
    try {
      await onSetActive(toggling, !toggling.is_active);
      setToggling(null);
    } catch (caught) {
      setToggleError(caught);
    }
  }

  const collator = new Intl.Collator(i18n.language, { sensitivity: 'base' });
  const rows =
    state.status === 'ready'
      ? state.categories
          .map((category) => ({ category, label: label(category) }))
          .sort(
            (a, b) =>
              Number(b.category.is_active) - Number(a.category.is_active) ||
              collator.compare(a.label, b.label),
          )
      : [];

  const badges = (category: ExpenseCategory) => (
    <span className="flex flex-wrap gap-1">
      {category.system_key ? (
        <DefaultBadge>{t(`${C}.default`)}</DefaultBadge>
      ) : null}
      <StateBadge active={category.is_active}>
        {t(`${C}.states.${category.is_active ? 'active' : 'inactive'}`)}
      </StateBadge>
    </span>
  );
  const toggleName = toggling ? label(toggling) : '';

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <ExpensesTabs />
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold">{t(`${C}.title`)}</h1>
          <Button type="button" className="min-h-11 md:min-h-9" onClick={onAdd}>
            {t(`${C}.add`)}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">{t(`${C}.intro`)}</p>
        {state.status === 'loading' ? <LoadingState /> : null}
        {state.status === 'error' ? (
          <ErrorAlert error={state.error} onRetry={state.onRetry} />
        ) : null}
        {state.status === 'ready' && state.refreshError ? (
          <ErrorAlert
            error={state.refreshError}
            message={t(`${C}.stale`)}
            onRetry={state.onRefresh}
            retryLabel={t('finance.reloadList')}
          />
        ) : null}
        {state.status === 'ready' && rows.length === 0 ? (
          <p data-categories-empty>{t(`${C}.empty`)}</p>
        ) : null}
        {rows.length > 0 ? (
          <>
            <div className="hidden md:block" data-categories-table>
              <Table>
                <TableCaption className="sr-only">
                  {t(`${C}.caption`)}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">{t(`${C}.columns.name`)}</TableHead>
                    <TableHead scope="col">{t(`${C}.columns.state`)}</TableHead>
                    <TableHead scope="col">
                      {t(`${C}.columns.actions`)}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map(({ category, label: text }) => (
                    <TableRow
                      key={category.id}
                      data-category-id={category.id}
                      className="align-top"
                    >
                      <TableCell className={`whitespace-normal ${NAME_CLASS}`}>
                        {text}
                      </TableCell>
                      <TableCell>{badges(category)}</TableCell>
                      <TableCell>
                        <Actions
                          category={category}
                          label={text}
                          onRename={onRename}
                          onToggle={startToggle}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 md:hidden" data-categories-cards>
              {rows.map(({ category, label: text }) => (
                <li
                  key={category.id}
                  data-category-card={category.id}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
                >
                  <p className={`font-medium ${NAME_CLASS}`}>{text}</p>
                  {badges(category)}
                  <Actions
                    category={category}
                    label={text}
                    onRename={onRename}
                    onToggle={startToggle}
                  />
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p role="status" aria-live="polite" className="text-sm">
          {announcement}
        </p>
      </div>
      <ConfirmDialog
        open={toggling !== null}
        title={
          toggling?.is_active
            ? t(`${C}.toggle.deactivateTitle`, { name: toggleName })
            : t(`${C}.toggle.reactivateTitle`, { name: toggleName })
        }
        body={
          toggling?.is_active
            ? t(`${C}.toggle.deactivateBody`)
            : t(`${C}.toggle.reactivateBody`)
        }
        confirmLabel={
          toggling?.is_active ? t(`${C}.deactivate`) : t(`${C}.reactivate`)
        }
        destructive={toggling?.is_active === true}
        alert={
          toggleError ? (
            <ErrorAlert
              error={toggleError}
              message={inUseMessage(toggleError)}
            />
          ) : null
        }
        onConfirm={confirmToggle}
        onCancel={() => {
          setToggling(null);
          setToggleError(null);
        }}
      />
    </div>
  );
}
