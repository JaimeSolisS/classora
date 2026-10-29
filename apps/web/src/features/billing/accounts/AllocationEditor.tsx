import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { FieldError } from '@/features/auth/FieldError';
import { useFieldMessage } from '@/features/scheduling/series/hooks';
import type { ChargeItem } from './api';
import { type Cents } from './cents';
import { useAccountLabels } from './labels';
import {
  fillOldestFirst,
  linesOfPlan,
  typedTotal,
  type LineErrors,
} from './rules';

// "Choose charges" (#379 D2, E1): the account's open charges oldest due
// first, each with an amount input (empty = not allocated), "Fill" (oldest
// first up to the money available), the running total and what is left
// (account credit for a payment, unused credit for a credit allocation).
// The dialogs check the rows on submit (a row above its open amount, or a
// total above the money, blocks the request).

export interface AllocationEditorProps {
  /** The open charges, oldest first. */
  charges: readonly ChargeItem[];
  /** Charge id → typed amount. */
  lines: Readonly<Record<string, string>>;
  onLines: (lines: Record<string, string>) => void;
  errors?: LineErrors;
  /** The money to allocate (null while the amount isn't valid). */
  available: Cents | null;
  /** What the remainder is called: account credit, or unused credit. */
  remainderKey:
    | 'billing.accounts.editor.remainderCredit'
    | 'billing.accounts.editor.remainderUnused';
  /** A prefix for the inputs' ids. */
  idPrefix: string;
  disabled?: boolean;
}

export function AllocationEditor({
  charges,
  lines,
  onLines,
  errors = {},
  available,
  remainderKey,
  idPrefix,
  disabled = false,
}: AllocationEditorProps) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const labels = useAccountLabels();

  if (charges.length === 0) {
    return <p className="text-sm">{t('billing.accounts.editor.noCharges')}</p>;
  }

  const total = typedTotal(charges, lines);
  const remainder = available === null ? null : ((available - total) as Cents);
  const over = remainder !== null && remainder < 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {t('billing.accounts.editor.help')}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || available === null || available <= 0}
          onClick={() =>
            onLines(linesOfPlan(fillOldestFirst(charges, available!)))
          }
        >
          {t('billing.accounts.editor.fill')}
        </Button>
      </div>
      <Table aria-label={t('billing.accounts.editor.label')}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              {t('billing.accounts.charges.columns.description')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.accounts.charges.columns.student')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.accounts.charges.columns.dueDate')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.accounts.charges.columns.open')}
            </TableHead>
            <TableHead scope="col">
              {t('billing.accounts.editor.amount')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {charges.map((charge) => {
            const inputId = `${idPrefix}-line-${charge.id}`;
            const errorId = `${inputId}-error`;
            const error = errors[charge.id];
            const label = labels.chargeLabel(charge);
            return (
              <TableRow key={charge.id} data-allocation-line={charge.id}>
                <TableCell className="max-w-48 whitespace-normal break-words">
                  {label}
                  <span className="block text-xs text-muted-foreground">
                    {labels.date(charge.service_date)}
                  </span>
                </TableCell>
                <TableCell className="max-w-40 whitespace-normal break-words">
                  {charge.student.name}
                </TableCell>
                <TableCell>{labels.date(charge.due_date)}</TableCell>
                <TableCell>{labels.money(charge.open)}</TableCell>
                <TableCell className="min-w-32">
                  <Input
                    id={inputId}
                    type="text"
                    inputMode="decimal"
                    autoComplete="off"
                    disabled={disabled}
                    value={lines[charge.id] ?? ''}
                    aria-label={t('billing.accounts.editor.amountFor', {
                      charge: label,
                      student: charge.student.name,
                      date: labels.date(charge.due_date),
                    })}
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? errorId : undefined}
                    onChange={(event) =>
                      onLines({ ...lines, [charge.id]: event.target.value })
                    }
                  />
                  <FieldError id={errorId} message={message(error)} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <p className="text-sm font-medium" aria-live="polite">
        {t('billing.accounts.editor.total', { amount: labels.cents(total) })}
        {remainder !== null && !over
          ? ` · ${t(remainderKey, { amount: labels.cents(remainder) })}`
          : ''}
      </p>
      {over ? (
        <p className="text-sm font-medium text-destructive">
          {t('billing.accounts.editor.over', {
            amount: labels.cents((0 - remainder) as Cents),
          })}
        </p>
      ) : null}
    </div>
  );
}
