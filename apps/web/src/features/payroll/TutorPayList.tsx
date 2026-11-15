import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Pagination } from '@/features/catalog/Pagination';
import { StateBadge } from '@/features/users/admin/StateBadge';
import { formatMoney } from '@/lib/money';
import type { PayrollTutor, PayrollTutorPage } from './api';
import { usePayLabels } from './labels';

// The payroll tutor list (#425 B), without any query: a table from 768 px
// and cards below it, with loading, empty and error states. The API sends
// no contact data and neither does this. `current_terms: null` covers both
// a tutor with no terms and a stopped one: the list response can't tell them
// apart, so one label says both (#425 decision D1).

export type TutorPayListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: PayrollTutorPage };

interface TutorPayListProps {
  state: TutorPayListState;
  /** The id of the heading that names the list. */
  labelledBy: string;
  /** Goes to another 1-based page. */
  onPage: (page: number) => void;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-all underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

function useRowText() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  return (tutor: PayrollTutor) => ({
    frequency: tutor.current_terms
      ? labels.frequency(tutor.current_terms.pay_frequency)
      : t('payroll.list.noCurrentTerms'),
    salary:
      tutor.current_terms?.salary_amount != null
        ? formatMoney(tutor.current_terms.salary_amount)
        : t('payroll.compensation.noSalary'),
    rates:
      tutor.current_rate_kinds.length > 0
        ? tutor.current_rate_kinds.map(labels.kind).join(', ')
        : t('payroll.compensation.noRate'),
  });
}

export function TutorPayList({ state, labelledBy, onPage }: TutorPayListProps) {
  const { t } = useTranslation();
  const rowText = useRowText();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('payroll.list.empty')}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="hidden md:block" data-payroll-table>
        <Table aria-labelledby={labelledBy}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">{t('payroll.list.name')}</TableHead>
              <TableHead scope="col">{t('payroll.list.username')}</TableHead>
              <TableHead scope="col">{t('payroll.list.state')}</TableHead>
              <TableHead scope="col">{t('payroll.list.frequency')}</TableHead>
              <TableHead scope="col">{t('payroll.list.salary')}</TableHead>
              <TableHead scope="col">{t('payroll.list.rates')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((tutor) => {
              const text = rowText(tutor);
              return (
                <TableRow key={tutor.user_id} data-tutor-id={tutor.user_id}>
                  <TableCell>
                    <Link
                      to={`/payroll/tutors/${tutor.user_id}`}
                      className={LINK_CLASS}
                    >
                      {tutor.display_name}
                    </Link>
                  </TableCell>
                  <TableCell>{tutor.username}</TableCell>
                  <TableCell>
                    <StateBadge state={tutor.state} />
                  </TableCell>
                  <TableCell>{text.frequency}</TableCell>
                  <TableCell>{text.salary}</TableCell>
                  <TableCell>{text.rates}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-payroll-cards>
        {items.map((tutor) => {
          const text = rowText(tutor);
          return (
            <li
              key={tutor.user_id}
              data-tutor-card={tutor.user_id}
              className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Link
                  to={`/payroll/tutors/${tutor.user_id}`}
                  className={LINK_CLASS}
                >
                  {tutor.display_name}
                </Link>
                <StateBadge state={tutor.state} />
              </div>
              <dl className="grid grid-cols-1 gap-1 text-sm">
                {(
                  [
                    [t('payroll.list.username'), tutor.username],
                    [t('payroll.list.frequency'), text.frequency],
                    [t('payroll.list.salary'), text.salary],
                    [t('payroll.list.rates'), text.rates],
                  ] as const
                ).map(([term, value]) => (
                  <div key={term} className="min-w-0">
                    <dt className="font-medium">{term}</dt>
                    <dd className="break-words">{value}</dd>
                  </div>
                ))}
              </dl>
            </li>
          );
        })}
      </ul>
      <Pagination
        offset={offset}
        limit={limit}
        shown={items.length}
        total={total}
        onPage={onPage}
        buttonClassName="min-h-11 md:min-h-9"
      />
    </div>
  );
}
