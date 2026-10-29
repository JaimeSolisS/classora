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
import type { UnbilledEnrollment, UnbilledEnrollmentPage } from './api';

// The Enrollments section of the Unbilled tab (#372 B), without any query
// (stories render each state): the group and course enrollment periods that
// have no fee charge, with the enrollment date (the local date in the
// center's zone), student, group or course, subject, family and reason, a
// fix link when the user can use it (no family → the families list for
// guardian.view holders; no fee → the price catalog for billing.manage
// holders, filtered to the class type), its own pagination and the note
// that per-session group enrollments are in the sessions table. Loading and
// errors are its own, apart from the sessions table. Hidden while billing
// is off (the tab says so above).

export type UnbilledEnrollmentsState =
  | { status: 'hidden' }
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; startsOn: string; page: UnbilledEnrollmentPage };

export interface UnbilledEnrollmentsProps {
  state: UnbilledEnrollmentsState;
  /** A reason is chosen (the "nothing with this reason" message). */
  filtered: boolean;
  onPage: (page: number) => void;
  /** guardian.view: the "Not in a family" fix link (the families list). */
  canOpenFamilies: boolean;
  /** billing.manage: the "No fee" fix link (the price catalog). */
  canManagePrices: boolean;
}

/** The group's or the course's name. */
function scopeName(item: UnbilledEnrollment): string {
  return item.group?.name ?? item.cohort?.name ?? '';
}

export function UnbilledEnrollments({
  state,
  filtered,
  onPage,
  canOpenFamilies,
  canManagePrices,
}: UnbilledEnrollmentsProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const titleId = useId();

  if (state.status === 'hidden') return null;

  function fixLink(item: UnbilledEnrollment) {
    if (item.reason === 'no_billing_account' && canOpenFamilies) {
      return (
        <Link
          to="/families"
          className={LINK_BUTTON_CLASS}
          aria-label={t('billing.unbilled.fix.familyFor', {
            student: item.student.name,
          })}
        >
          {t('billing.unbilled.fix.family')}
        </Link>
      );
    }
    if (item.reason === 'no_price' && canManagePrices) {
      const classType = item.kind === 'cohort_enrollment' ? 'course' : 'group';
      return (
        <Link
          to={`/billing/prices?class_type=${classType}`}
          className={LINK_BUTTON_CLASS}
          aria-label={t('billing.unbilled.enrollments.fix.priceFor', {
            name: scopeName(item),
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
      <p data-unbilled-enrollments-empty="filtered">
        {t('billing.unbilled.enrollments.emptyFiltered')}
      </p>
    );
  } else if (state.page.items.length === 0) {
    body = (
      <p data-unbilled-enrollments-empty="on">
        {t('billing.unbilled.enrollments.emptyOn', {
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
                {t('billing.unbilled.enrollments.columns.date')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.enrollments.columns.scope')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.subject')}
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
                key={item.enrollment_id}
                data-unbilled-enrollment-id={item.enrollment_id}
              >
                <TableCell>{formatLocalDate(item.service_date)}</TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.student.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  <span className="block">{scopeName(item)}</span>
                  <span className="block text-sm text-muted-foreground">
                    {t(`billing.unbilled.enrollments.kinds.${item.kind}`)}
                  </span>
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.subject.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.family?.name ?? t('billing.unbilled.noFamily')}
                </TableCell>
                <TableCell>
                  {t(`billing.unbilled.enrollments.reasons.${item.reason}`)}
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
          label={t('billing.unbilled.enrollments.pagination')}
        />
      </div>
    );
  }

  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-4"
      data-unbilled-section="enrollments"
    >
      <h2 id={titleId} className="text-lg font-semibold">
        {t('billing.unbilled.enrollments.title')}
      </h2>
      {body}
      <p className="text-sm text-muted-foreground">
        {t('billing.unbilled.enrollments.note')}
      </p>
    </section>
  );
}
