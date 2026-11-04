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
import { INPUT_CLASS, SHEET_CLASS } from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import type { ExpenseTemplate } from '../api';
import { FormField } from '../expenses/FormField';
import type { FinanceMessage } from '../forms';
import { RowNoticeAlert } from '../RowNoticeAlert';
import { useRowSave, type RowNotice, type SaveResult } from '../useRowSave';
import {
  earliestEnd,
  endServerErrors,
  initialEnd,
  validateEnd,
  type EndFormValues,
} from './rules';

// "Finalizar" (M7-13b D4), without any API call: sets `ends_on`, by default
// the center's today, never before the latest generated period (the date
// input's minimum, the client check and the server's
// `expense_template_ends_on_invalid` on the field). The #458 config-page
// rules come from useRowSave, as in the edit dialog.

export interface EndTemplateDialogProps {
  open: boolean;
  /** The template, as the page has it now. */
  template: ExpenseTemplate;
  today: LocalDate;
  onSubmit: (
    values: EndFormValues,
    base: ExpenseTemplate,
  ) => Promise<SaveResult>;
  onReload: () => Promise<boolean>;
  onClose: (saved: boolean) => void;
  /** Stories. */
  initialValues?: EndFormValues;
  initialErrors?: Partial<Record<'endsOn', FinanceMessage>>;
  initialNotice?: RowNotice;
}

export function EndTemplateDialog(props: EndTemplateDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose(false);
      }}
    >
      {props.open ? <Body {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const D = 'finance.templates.dialog';

function Body({
  template,
  today,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors,
  initialNotice,
  busyRef,
}: EndTemplateDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const fieldId = `${id}-endsOn`;
  const form = useRowSave<ExpenseTemplate, EndFormValues, 'endsOn'>({
    row: template,
    initial: (row) => initialEnd(row ?? template, today),
    initialValues,
    initialErrors,
    initialNotice,
    validate: (values) => validateEnd(values, template),
    serverErrors: endServerErrors,
    onSubmit: (values, base) => onSubmit(values, base ?? template),
    onReload,
    onClose,
    focusFirst: () => {
      // After the render that enables the field again.
      window.setTimeout(() => document.getElementById(fieldId)?.focus());
    },
    busyRef,
  });
  const min = earliestEnd(template);

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-template-dialog="end"
    >
      <DialogHeader>
        <DialogTitle>{t(`${D}.endTitle`, { name: template.name })}</DialogTitle>
        <DialogDescription>{t(`${D}.endHelp`)}</DialogDescription>
      </DialogHeader>
      <RowNoticeAlert notice={form.notice} kind="templates" />
      {form.failure ? (
        <ErrorAlert
          error={form.failure}
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
        className="flex min-w-0 flex-col gap-4"
      >
        <FormField
          id={fieldId}
          label={t(`${D}.endsOnRequired`)}
          hint={
            template.last_period_start
              ? t(`${D}.endMin`, { date: formatLocalDate(min) })
              : undefined
          }
          error={message(form.errors.endsOn)}
        >
          {(field) => (
            <Input
              {...field}
              type="date"
              min={min}
              className={INPUT_CLASS}
              value={form.values.endsOn}
              disabled={form.locked}
              onChange={(event) =>
                form.setValues({ endsOn: event.target.value })
              }
            />
          )}
        </FormField>
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
            variant="destructive"
            className="min-h-11 md:min-h-9"
            disabled={form.locked}
          >
            {form.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : (
              t(`${D}.submitEnd`)
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
