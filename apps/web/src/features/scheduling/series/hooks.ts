import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

// Hooks of the series dialogs (#54b C4): the text of a field message (a
// `common` key, or an `errors` key prefixed with `errors:`), and the focus
// return to the button that opened a dialog.

export function useFieldMessage(): (
  key: string | undefined,
) => string | undefined {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  return (key) => {
    if (!key) {
      return undefined;
    }
    return key.startsWith('errors:')
      ? tErrors(key.slice('errors:'.length) as 'validation_error')
      : t(key as 'scheduling.validation.dateRequired');
  };
}

/**
 * The DialogContent handlers that put the focus back on the element that
 * had it when the dialog opened (the trigger), when it is still there.
 * Radix only does that for its own DialogTrigger.
 *
 * The opener is read when the dialog opens, so nothing inside the dialog
 * may take the focus before that (no `autoFocus` on a field; focus it from
 * `onOpenAutoFocus` instead, #234 QA D1). When the page already moved the
 * focus somewhere else on close (a status notice whose trigger is gone),
 * it stays there.
 */
export function useReturnFocus() {
  const opener = useRef<HTMLElement | null>(null);
  return {
    onOpenAutoFocus: () => {
      opener.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    },
    onCloseAutoFocus: (event: Event) => {
      event.preventDefault();
      const active = document.activeElement;
      const movedElsewhere =
        active instanceof HTMLElement &&
        active !== document.body &&
        active.isConnected &&
        active !== opener.current;
      if (!movedElsewhere && opener.current?.isConnected) {
        opener.current.focus();
      }
      opener.current = null;
    },
  };
}
