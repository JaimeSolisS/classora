import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Pagination } from '@/features/catalog/Pagination';
import { useAccountLabels } from '../accounts/labels';
import type { WithdrawalItem, WithdrawalPage } from './api';
import { useWithdrawalLabels } from './labels';

// The family account page's "Withdrawals" section (#387 B4), without any
// query: the family's withdrawals, newest first (date in the center zone,
// student, subject, outcome, credit, refund), paginated; "Details" opens
// the drawer with the stored preview.

export type WithdrawalsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: WithdrawalPage };

export interface WithdrawalsSectionProps {
  state: WithdrawalsState;
  /** The family's students by id (the summary's). */
  studentNames: Readonly<Record<string, string>>;
  onPage: (page: number) => void;
  onOpen: (item: WithdrawalItem) => void;
}

export function WithdrawalsSection({
  state,
  studentNames,
  onPage,
  onOpen,
}: WithdrawalsSectionProps) {
  const { t } = useTranslation();
  const money = useAccountLabels();
  const labels = useWithdrawalLabels();
  const id = useId();

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={2} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0) {
    body = <p className="text-sm">{t('billing.withdrawals.section.empty')}</p>;
  } else {
    const { items, total, limit, offset } = state.page;
    body = (
      <div className="flex flex-col gap-4">
        <div className="overflow-x-auto">
          <Table aria-labelledby={`${id}-title`}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.withdrawals.section.columns.date')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.withdrawals.section.columns.student')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.withdrawals.section.columns.subject')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.withdrawals.section.columns.outcome')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.withdrawals.section.columns.credit')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.withdrawals.section.columns.refund')}
                </TableHead>
                <TableHead scope="col">
                  <span className="sr-only">
                    {t('billing.withdrawals.section.columns.actions')}
                  </span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const student = studentNames[item.student_id] ?? '—';
                return (
                  <TableRow key={item.id} data-withdrawal-id={item.id}>
                    <TableCell>
                      {money.instantDate(item.withdrawn_at)}
                    </TableCell>
                    <TableCell className="whitespace-normal break-words">
                      {student}
                    </TableCell>
                    <TableCell>
                      {labels.subjectKind(item.subject.kind)}
                    </TableCell>
                    <TableCell>{labels.outcome(item.outcome)}</TableCell>
                    <TableCell>{money.money(item.amount)}</TableCell>
                    <TableCell>{money.money(item.refund_amount)}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('billing.withdrawals.section.viewFor', {
                          student,
                          date: money.instantDate(item.withdrawn_at),
                        })}
                        onClick={() => onOpen(item)}
                      >
                        {t('billing.withdrawals.section.view')}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
        <Pagination
          offset={offset}
          limit={limit}
          shown={items.length}
          total={total}
          onPage={onPage}
        />
      </div>
    );
  }

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('billing.withdrawals.section.title')}
      </h2>
      {body}
    </section>
  );
}
