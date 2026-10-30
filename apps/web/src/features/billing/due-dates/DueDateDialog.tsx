import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import { Counter } from '@/features/academics/Counter';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { ChargeItem } from '../accounts/api';
import { useAccountLabels } from '../accounts/labels';
import type { DueDateChange, DueDateChangeCreate } from './api';
import {
  becomesOverdue,
  DUE_DATE_MAX,
  DUE_DATE_MIN,
  dueDateRefusal,
  REASON_MAX,
  validateDueDateForm,
  type DueDateErrors,
  type DueDateFormValues,
} from './rules';

// "Change due date" (#382 D2–D4), without any API call: the charge
// (student, kind, description, open amount, current and original due
// dates), the new date (the current one to start with) and a required
// reason (1–500, with a counter), then the charge's change history, oldest
// first. A date before the center's today shows a warning, not an error
// (D4). `onSubmit` resolves once the change is stored; the dialog stays
// open, busy, until then (no optimistic update). `409 due_date_unchanged`
// and a 422 go on the fields; `409 charge_settled` and `404` show their
// message and call `onStale` (the page refetches the charges); anything
// else (a 403 included) is an alert. Nothing typed is lost on any error.

export type HistoryState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: DueDateChange[] };

export interface DueDateDialogProps {
  open: boolean;
  charge: ChargeItem | null;
  /** The center's today (the D4 warning). */
  today: string;
  history: HistoryState;
  /** Stores the change; rejects with the error to show. */
  onSubmit: (body: DueDateChangeCreate) => Promise<void>;
  /** The charge was settled or is gone: refetch the charges. */
  onStale: () => void;
  onClose: () => void;
  /** Stories: the values, field errors and alert to start from. */
  initialValues?: DueDateFormValues;
  initialErrors?: DueDateErrors;
  initialFailure?: unknown;
}

export function DueDateDialog(props: DueDateDialogProps) {
  const busy = useRef(false);
  const { charge } = props;
  return (
    <Dialog
      open={props.open && charge !== null}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open && charge ? (
        <DialogBody key={charge.id} {...props} charge={charge} busyRef={busy} />
      ) : null}
    </Dialog>
  );
}

function DialogBody({
  charge,
  today,
  history,
  onSubmit,
  onStale,
  onClose,
  initialValues,
  initialErrors,
  initialFailure,
  busyRef,
}: DueDateDialogProps & {
  charge: ChargeItem;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<DueDateFormValues>(
    () => initialValues ?? { dueDate: charge.due_date, reason: '' },
  );
  const [errors, setErrors] = useState<DueDateErrors>(initialErrors ?? {});
  const [failure, setFailure] = useState<unknown>(initialFailure ?? null);
  const [submitting, setSubmitting] = useState(false);
  const ids = {
    date: `${id}-date`,
    dateWarning: `${id}-date-warning`,
    dateError: `${id}-date-error`,
    reason: `${id}-reason`,
    reasonHint: `${id}-reason-hint`,
    reasonError: `${id}-reason-error`,
    counter: `${id}-counter`,
    history: `${id}-history`,
  };
  const past = becomesOverdue(values.dueDate, today);

  function focusFirst(found: DueDateErrors) {
    const target = found.dueDate ? ids.date : found.reason ? ids.reason : null;
    if (target) document.getElementById(target)?.focus();
  }

  async function submit() {
    setFailure(null);
    const found = validateDueDateForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit({
        due_date: values.dueDate,
        reason: values.reason.trim(),
      });
      busyRef.current = false;
      onClose();
      return;
    } catch (caught) {
      const refusal = dueDateRefusal(caught);
      if (refusal.kind === 'fields') {
        setErrors(refusal.errors);
        // After the busy state ends, so the field can take focus.
        window.setTimeout(() => focusFirst(refusal.errors), 0);
      } else {
        setFailure(caught);
        if (refusal.kind === 'settled' || refusal.kind === 'gone') onStale();
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    void submit();
  }

  const dateMessage = message(errors.dueDate);
  const reasonMessage = message(errors.reason);
  const details: [string, string][] = [
    [t('billing.dueDates.change.student'), charge.student.name],
    [t('billing.dueDates.change.kind'), labels.chargeKind(charge.kind)],
    [t('billing.dueDates.change.description'), charge.description ?? '—'],
    [t('billing.dueDates.change.open'), labels.money(charge.open)],
    [t('billing.dueDates.change.current'), labels.date(charge.due_date)],
    [
      t('billing.dueDates.change.original'),
      labels.date(charge.original_due_date),
    ],
  ];

  let historyBody;
  if (history.status === 'loading') {
    historyBody = <LoadingState rows={2} />;
  } else if (history.status === 'error') {
    historyBody = (
      <ErrorAlert error={history.error} onRetry={history.onRetry} />
    );
  } else if (history.items.length === 0) {
    historyBody = (
      <p className="text-sm">{t('billing.dueDates.history.empty')}</p>
    );
  } else {
    historyBody = (
      <ol className="flex flex-col gap-3" aria-labelledby={ids.history}>
        {history.items.map((change) => (
          <li
            key={change.id}
            data-due-date-change={change.id}
            className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3 text-sm"
          >
            <span className="font-medium">
              {t('billing.dueDates.history.line', {
                from: labels.date(change.previous_due_date),
                to: labels.date(change.due_date),
              })}
            </span>
            <span className="break-words whitespace-pre-line">
              {change.reason}
            </span>
            <span className="text-muted-foreground">
              {t('billing.dueDates.history.changedOn', {
                date: labels.instantDate(change.created_at),
              })}
            </span>
          </li>
        ))}
      </ol>
    );
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-lg [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.dueDates.change.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.dueDates.change.help')}
        </DialogDescription>
      </DialogHeader>
      <dl className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
        {details.map(([term, value]) => (
          <div key={term} className="flex min-w-0 flex-col">
            <dt className="text-muted-foreground">{term}</dt>
            <dd className="font-medium break-words">{value}</dd>
          </div>
        ))}
      </dl>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <div className="flex max-w-xs flex-col gap-2">
          <label htmlFor={ids.date} className="text-sm font-medium">
            {t('billing.dueDates.change.newDate')}
          </label>
          <Input
            id={ids.date}
            type="date"
            min={DUE_DATE_MIN}
            max={DUE_DATE_MAX}
            value={values.dueDate}
            aria-invalid={dateMessage ? true : undefined}
            aria-describedby={
              [
                past ? ids.dateWarning : null,
                dateMessage ? ids.dateError : null,
              ]
                .filter(Boolean)
                .join(' ') || undefined
            }
            onChange={(event) =>
              setValues({ ...values, dueDate: event.target.value })
            }
          />
          {past ? (
            <p
              id={ids.dateWarning}
              className="text-sm font-medium"
              data-due-date-warning
            >
              {t('billing.dueDates.change.pastWarning')}
            </p>
          ) : null}
          <FieldError id={ids.dateError} message={dateMessage} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.reason} className="text-sm font-medium">
            {t('billing.dueDates.change.reason')}
          </label>
          <textarea
            id={ids.reason}
            rows={3}
            className={TEXTAREA_CLASS}
            value={values.reason}
            aria-required="true"
            aria-invalid={reasonMessage ? true : undefined}
            aria-describedby={[
              ids.reasonHint,
              reasonMessage ? ids.reasonError : null,
              ids.counter,
            ]
              .filter(Boolean)
              .join(' ')}
            onChange={(event) =>
              setValues({ ...values, reason: event.target.value })
            }
          />
          <p id={ids.reasonHint} className="text-sm text-muted-foreground">
            {t('billing.dueDates.change.reasonHint')}
          </p>
          <FieldError id={ids.reasonError} message={reasonMessage} />
          <Counter id={ids.counter} value={values.reason} max={REASON_MAX} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.dueDates.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.dueDates.saving')} showLabel />
            ) : (
              t('billing.dueDates.change.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
      <section aria-labelledby={ids.history} className="flex flex-col gap-3">
        <h3 id={ids.history} className="font-semibold">
          {t('billing.dueDates.history.title')}
        </h3>
        {historyBody}
      </section>
    </DialogContent>
  );
}
