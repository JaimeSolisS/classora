import { useId } from 'react';
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
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { usePriceLabels } from '../pricing/labels';
import type { UnbilledTuition, UnbilledTuitionPage } from './api';

// The Tuition section of the Unbilled tab (#374 F1), without any query
// (stories render each state): the due tuition periods the daily job
// skips, with the period, student, plan (subject, group or "Individual",
// frequency), family or "No family" and reason, a fix link when the user
// can use it (no price → the per-period prices of the class type for
// billing.manage holders; family changed → the plan's page), its own
// pagination, loading and errors. Hidden while billing is off (the tab
// says so above). `filtered` is set when the tab's reason has no tuition
// counterpart or narrows it.

export type UnbilledTuitionState =
  | { status: 'hidden' }
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; startsOn: string; page: UnbilledTuitionPage };

export interface UnbilledTuitionProps {
  state: UnbilledTuitionState;
  /** A reason is chosen (the "nothing with this reason" message). */
  filtered: boolean;
  onPage: (page: number) => void;
  /** billing.manage: the "No price" fix link (the price catalog). */
  canManagePrices: boolean;
}

export function UnbilledTuitionSection({
  state,
  filtered,
  onPage,
  canManagePrices,
}: UnbilledTuitionProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const prices = usePriceLabels();
  const titleId = useId();

  if (state.status === 'hidden') return null;

  function fixLink(item: UnbilledTuition) {
    if (item.reason === 'family_changed') {
      return (
        <Link
          to={`/billing/tuition-plans/${item.tuition_plan_id}`}
          className={LINK_BUTTON_CLASS}
          aria-label={t('billing.unbilled.tuition.fix.planFor', {
            student: item.student.name,
          })}
        >
          {t('billing.unbilled.tuition.fix.plan')}
        </Link>
      );
    }
    if (item.reason === 'no_price' && canManagePrices) {
      const classType = item.group ? 'group' : 'individual';
      return (
        <Link
          to={`/billing/prices?class_type=${classType}&pricing_model=per_period`}
          className={LINK_BUTTON_CLASS}
          aria-label={t('billing.unbilled.tuition.fix.priceFor', {
            subject: item.subject.name,
          })}
        >
          {t('billing.unbilled.fix.price')}
        </Link>
      );
    }
    return null;
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={3} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && filtered) {
    body = (
      <p data-unbilled-tuition-empty="filtered">
        {t('billing.unbilled.tuition.emptyFiltered')}
      </p>
    );
  } else if (state.page.items.length === 0) {
    body = (
      <p data-unbilled-tuition-empty="on">
        {t('billing.unbilled.tuition.emptyOn', {
          date: formatLocalDate(state.startsOn),
        })}
      </p>
    );
  } else {
    const { items, total, limit, offset } = state.page;
    body = (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={titleId}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.unbilled.tuition.columns.period')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.tuition.columns.plan')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.family')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.reason')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.unbilled.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow
                key={`${item.tuition_plan_id}:${item.period_start}`}
                data-unbilled-tuition-plan-id={item.tuition_plan_id}
              >
                <TableCell>
                  {t('billing.tuition.periodRange', {
                    from: formatLocalDate(item.period_start),
                    to: formatLocalDate(item.period_end),
                  })}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.student.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  <span className="block">{item.subject.name}</span>
                  <span className="block text-sm text-muted-foreground">
                    {t('billing.tuition.scopeLine', {
                      scope:
                        item.group?.name ?? t('billing.tuition.individual'),
                      frequency: prices.frequency(item.frequency),
                    })}
                  </span>
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.family?.name ?? t('billing.unbilled.noFamily')}
                </TableCell>
                <TableCell>
                  {t(`billing.unbilled.tuition.reasons.${item.reason}`)}
                </TableCell>
                <TableCell className="text-right">{fixLink(item)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Pagination
          offset={offset}
          limit={limit}
          shown={items.length}
          total={total}
          onPage={onPage}
          label={t('billing.unbilled.tuition.pagination')}
        />
      </div>
    );
  }

  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-4"
      data-unbilled-section="tuition"
    >
      <h2 id={titleId} className="text-lg font-semibold">
        {t('billing.unbilled.tuition.title')}
      </h2>
      {body}
    </section>
  );
}
