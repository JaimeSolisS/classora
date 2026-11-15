import { useTranslation } from 'react-i18next';
import type { ExpenseCategory, SystemKey } from './api';

// A category's label (#114 E3, M7-11b E2): while a seeded category keeps its
// `system_key`, it shows the web catalog's label in the viewer's language;
// once renamed, it shows its stored name in every language. The pickers of
// #115b, #116b and the reports of #117 use the same rule.

export const SYSTEM_KEYS: readonly SystemKey[] = [
  'rent',
  'utilities',
  'supplies',
  'equipment',
  'maintenance',
];

const KNOWN = new Set<string>(SYSTEM_KEYS);

export function useCategoryLabel(): (
  category: Pick<ExpenseCategory, 'name' | 'system_key'>,
) => string {
  const { t } = useTranslation();
  return (category) =>
    category.system_key && KNOWN.has(category.system_key)
      ? t(`finance.categoryLabels.${category.system_key}`)
      : category.name;
}
