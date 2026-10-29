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
import {
  LINK_BUTTON_CLASS,
  SELECT_CLASS,
  TEXT_LINK_CLASS,
} from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { BillingTabs } from '../BillingTabs';
import type { BillingSettings } from '../settings/api';
import {
  UNBILLED_REASONS,
  type UnbilledFilters,
  type UnbilledItem,
  type UnbilledPage,
} from './api';
import {
  UnbilledEnrollments,
  type UnbilledEnrollmentsState,
} from './UnbilledEnrollments';
import {
  UnbilledTuitionSection,
  type UnbilledTuitionState,
} from './UnbilledTuition';

// The Unbilled tab (#366 C, #372), without any query (stories render each
// state): the Billing tabs, the title with the note that new confirmations
// are billed within about 15 minutes, the reason filter (the page keeps it
// in the URL; it applies to both sections), the Sessions table: date and
// time in the center's zone, student, subject, class (individual or the
// group), length, family and reason, with a link to fix the cause when the
// user can use it (no family → the families list for guardian.view
// holders; no price → the price catalog for billing.manage holders), and
// below it the Enrollments section (UnbilledEnrollments) and the Tuition
// section (UnbilledTuition, #374 F1). While billing is off they give way to a link to the Settings tab. Below `md` the tables
// scroll inside their own container (SC 1.4.10 data-table exception).

export type UnbilledState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; settings: BillingSettings; page: UnbilledPage };

export interface UnbilledViewProps {
  state: UnbilledState;
  /** The Enrollments section, loaded and failing on its own. */
  enrollments: UnbilledEnrollmentsState;
  /** The Tuition section, loaded and failing on its own. */
  tuition: UnbilledTuitionState;
  filters: UnbilledFilters;
  onFilters: (filters: UnbilledFilters) => void;
  onPage: (page: number) => void;
  onEnrollmentPage: (page: number) => void;
  onTuitionPage: (page: number) => void;
  /** guardian.view: the "Not in a family" fix link (the families list). */
  canOpenFamilies: boolean;
  /** billing.manage: the "No price" fix link (the price catalog). */
  canManagePrices: boolean;
}

export function UnbilledView({
  state,
  enrollments,
  tuition,
  filters,
  onFilters,
  onPage,
  onEnrollmentPage,
  onTuitionPage,
  canOpenFamilies,
  canManagePrices,
}: UnbilledViewProps) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    sessions: `${id}-sessions`,
    reason: `${id}-reason`,
  };

  function fixLink(item: UnbilledItem) {
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
      return (
        <Link
          to="/billing/prices"
          className={LINK_BUTTON_CLASS}
          aria-label={t('billing.unbilled.fix.priceFor', {
            subject: item.subject.name,
          })}
        >
          {t('billing.unbilled.fix.price')}
        </Link>
      );
    }
    return null;
  }

  const billingOff =
    state.status === 'ready' && state.settings.billing_starts_on === null;
  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.settings.billing_starts_on === null) {
    body = (
      <div
        className="flex flex-col items-start gap-3"
        data-unbilled-empty="off"
      >
        <p>{t('billing.unbilled.emptyOff')}</p>
        <Link to="/billing/settings" className={TEXT_LINK_CLASS}>
          {t('billing.unbilled.openSettings')}
        </Link>
      </div>
    );
  } else if (state.page.items.length === 0 && filters.reason) {
    body = (
      <p data-unbilled-empty="filtered">
        {t('billing.unbilled.emptyFiltered')}
      </p>
    );
  } else if (state.page.items.length === 0) {
    body = (
      <p data-unbilled-empty="on">
        {t('billing.unbilled.emptyOn', {
          date: formatLocalDate(state.settings.billing_starts_on),
        })}
      </p>
    );
  } else {
    const { items, total, limit, offset } = state.page;
    body = (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={ids.sessions}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.unbilled.columns.when')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.subject')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.class')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.unbilled.columns.length')}
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
                key={`${item.class_session_id}:${item.student.id}`}
                data-unbilled-session-id={item.class_session_id}
              >
                <TableCell>{formatInstant(item.starts_at)}</TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.student.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.subject.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.class_type === 'group' && item.group
                    ? item.group.name
                    : t('billing.unbilled.individual')}
                </TableCell>
                <TableCell>
                  {t('billing.unbilled.minutes', {
                    minutes: item.scheduled_minutes,
                  })}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {item.family?.name ?? t('billing.unbilled.noFamily')}
                </TableCell>
                <TableCell>
                  {t(`billing.unbilled.reasons.${item.reason}`)}
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
          label={t('billing.unbilled.sessions.pagination')}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-2">
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('billing.unbilled.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('billing.unbilled.intro')}
        </p>
        <p className="text-sm text-muted-foreground">
          {t('billing.unbilled.delay')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.unbilled.filters.label')}
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:max-w-2xl"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={ids.reason} className="text-sm font-medium">
            {t('billing.unbilled.filters.reason')}
          </label>
          <select
            id={ids.reason}
            className={SELECT_CLASS}
            value={filters.reason ?? ''}
            onChange={(event) =>
              onFilters({
                reason:
                  UNBILLED_REASONS.find(
                    (reason) => reason === event.target.value,
                  ) ?? null,
                page: 1,
                enrollmentPage: 1,
                tuitionPage: 1,
              })
            }
          >
            <option value="">{t('billing.unbilled.filters.all')}</option>
            {UNBILLED_REASONS.map((reason) => (
              <option key={reason} value={reason}>
                {t(`billing.unbilled.reasons.${reason}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {billingOff ? (
        body
      ) : (
        <>
          <section
            aria-labelledby={ids.sessions}
            className="flex flex-col gap-4"
            data-unbilled-section="sessions"
          >
            <h2 id={ids.sessions} className="text-lg font-semibold">
              {t('billing.unbilled.sessions.title')}
            </h2>
            {body}
          </section>
          <UnbilledEnrollments
            state={enrollments}
            filtered={filters.reason !== null}
            onPage={onEnrollmentPage}
            canOpenFamilies={canOpenFamilies}
            canManagePrices={canManagePrices}
          />
          <UnbilledTuitionSection
            state={tuition}
            filtered={filters.reason !== null}
            onPage={onTuitionPage}
            canManagePrices={canManagePrices}
          />
        </>
      )}
    </div>
  );
}
