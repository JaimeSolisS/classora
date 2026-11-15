import { useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { SHEET_CLASS } from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { ExpenseCategory } from '../api';
import {
  categoryServerErrors,
  validateCategoryForm,
  type CategoryErrors,
  type CategoryFormValues,
} from '../forms';
import { useCategoryLabel } from '../labels';
import { RowNoticeAlert } from '../RowNoticeAlert';
import { useRowSave, type RowNotice, type SaveResult } from '../useRowSave';

// Add a category, or rename one (M7-11b B2, B3, B5, D2), without any API
// call. A rename starts from the displayed label; on a keyed row it warns
// that the typed text shows in both languages for good. A taken name goes on
// the field, with the hint that a deactivated row of that name may exist.

export interface CategoryDialogProps {
  open: boolean;
  /** The category being renamed, as the list has it now; null adds one. */
  category: ExpenseCategory | null;
  /** Stores `values` based on `base`, the snapshot they were edited from;
   * rejects with the error to show. */
  onSubmit: (
    values: CategoryFormValues,
    base: ExpenseCategory | null,
  ) => Promise<SaveResult>;
  /** Refetches the list; resolves whether it reloaded. */
  onReload: () => Promise<boolean>;
  /** The dialog closed: `saved` after a stored change. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the errors to start from. */
  initialValues?: CategoryFormValues;
  initialErrors?: CategoryErrors;
  initialNotice?: RowNotice;
}

export function CategoryDialog(props: CategoryDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose(false);
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const D = 'finance.categories.dialog';

function DialogBody({
  category,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors,
  initialNotice,
  busyRef,
}: CategoryDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const label = useCategoryLabel();
  const id = useId();
  const ids = {
    name: `${id}-name`,
    error: `${id}-name-error`,
    hint: `${id}-name-hint`,
    keyed: `${id}-keyed`,
  };
  const form = useRowSave({
    row: category,
    initial: (row): CategoryFormValues => ({ name: row ? label(row) : '' }),
    initialValues,
    initialErrors,
    initialNotice,
    validate: validateCategoryForm,
    serverErrors: categoryServerErrors,
    onSubmit,
    onReload,
    onClose,
    focusFirst: (found) => {
      if (found.name) {
        document.getElementById(ids.name)?.focus();
      }
    },
    busyRef,
  });
  const { base, values, errors, failure } = form;
  const renaming = base !== null;
  const keyed = base?.system_key != null;
  const taken = errors.name === 'errors:expense_category_exists';
  const describedBy =
    [
      keyed ? ids.keyed : '',
      errors.name ? ids.error : '',
      taken ? ids.hint : '',
    ]
      .filter(Boolean)
      .join(' ') || undefined;

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {renaming ? t(`${D}.renameTitle`) : t(`${D}.addTitle`)}
        </DialogTitle>
        <DialogDescription>
          {renaming ? t(`${D}.renameHelp`) : t(`${D}.addHelp`)}
        </DialogDescription>
      </DialogHeader>
      <RowNoticeAlert notice={form.notice} kind="categories" />
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={
            form.conflict && !form.reloading
              ? () => void form.reload()
              : undefined
          }
          retryLabel={form.conflict ? t('finance.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={form.onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.name} className="text-sm font-medium">
            {t(`${D}.name`)}
          </label>
          <Input
            id={ids.name}
            autoComplete="off"
            className="h-11 md:h-9"
            value={values.name}
            disabled={form.locked}
            aria-invalid={errors.name ? true : undefined}
            aria-describedby={describedBy}
            onChange={(event) => form.setValues({ name: event.target.value })}
          />
          {keyed ? (
            <p
              id={ids.keyed}
              className="text-sm text-muted-foreground"
              data-keyed-warning
            >
              {t(`${D}.keyedWarning`)}
            </p>
          ) : null}
          <FieldError id={ids.error} message={message(errors.name)} />
          {taken ? (
            <p id={ids.hint} className="text-sm" data-exists-hint>
              {t(`${D}.existsHint`)}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={form.submitting}
            onClick={() => onClose(false)}
          >
            {t('finance.cancel')}
          </Button>
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={form.locked}
          >
            {form.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : (
              t('finance.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
