import { expect, type Locator } from '@playwright/test';

// The type-ahead combobox (#545, components/ui/combobox.tsx): a text field
// with role "combobox" whose options sit in a portalled listbox named by the
// same label (outside `scope`, even in a dialog). Works in local and server
// mode: the option is awaited, whichever answers it.

/** The combobox labelled `label` inside `scope`. */
export function comboboxField(scope: Locator, label: string): Locator {
  return scope.getByRole('combobox', { name: label, exact: true });
}

/** The open list of the combobox labelled `label`. */
export function comboboxOptions(scope: Locator, label: string): Locator {
  return scope.page().getByRole('listbox', { name: label, exact: true });
}

/** Types `query` into the combobox, then clicks the option named `name`. */
export async function pickOption(
  scope: Locator,
  label: string,
  query: string,
  name: string,
): Promise<void> {
  await comboboxField(scope, label).fill(query);
  const option = comboboxOptions(scope, label).getByRole('option', {
    name,
    exact: true,
  });
  await expect(option).toBeVisible();
  await option.click();
}
