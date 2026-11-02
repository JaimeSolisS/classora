import { X } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { useFormatters } from '@/lib/formatters';
import {
  PAY_ITEMS_ANCHOR,
  UNRESOLVED_KEY,
  type PayrollTutor,
  type UnresolvedFilters,
  type UnresolvedItem,
  type UnresolvedReason,
} from './api';
import { usePayLabels } from './labels';
import { INPUT_CLASS, SELECT_CLASS } from './sheet';
import { UNRESOLVED_REASONS } from './unresolvedUrl';

// The Unresolved work tab (#444 C), without any query: a table from 768 px
// and cards below it, the tutor (a search over the Payroll list) and reason
// filters, and each item's reason in plain words with the way to fix it. It
// shows no amount, and the API sends none (C5). "Everything has pay" is only
// said when it's true: not under a filter, and not while payroll is off.

export type UnresolvedState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly UnresolvedItem[];
      hasMore: boolean;
      loadingMore: boolean;
      /** The next page failed; the rows above stay. */
      moreError: unknown;
      onMore: () => void;
    };

/** Whether payroll is on, from the settings (a list read shows nothing). */
export type PayrollOffState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; off: boolean };

export interface UnresolvedWorkViewProps {
  state: UnresolvedState;
  payroll: PayrollOffState;
  filters: UnresolvedFilters;
  onFilters: (next: Partial<UnresolvedFilters>) => void;
  searchTutors: (q: string, signal: AbortSignal) => Promise<PayrollTutor[]>;
  /** The chosen tutor's name, for the chip. */
  tutorName: string | null;
  /** Remembers a picked tutor's name by id. */
  onTutorName: (id: string, name: string) => void;
  /** schedule.view: links each item to its session (C3). */
  canOpenSession: boolean;
}

const LINK_CLASS =
  'inline-flex min-h-11 items-center font-medium break-words underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

/** A reason that a change on the tutor's pay page fixes (C2). */
function fixedOnPayPage(reason: UnresolvedReason): boolean {
  return reason === 'no_pay_terms' || reason === 'no_rate';
}

function Reason({ item }: { item: UnresolvedItem }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      <span className="break-words">
        {t(`payroll.unresolved.reasons.${item.reason}`)}
      </span>
      {fixedOnPayPage(item.reason) ? (
        <Link
          to={`/payroll/tutors/${encodeURIComponent(item.tutor_user_id)}`}
          aria-label={t('payroll.unresolved.fixFor', {
            name: item.tutor_display_name,
          })}
          className={LINK_CLASS}
        >
          {t('payroll.unresolved.fix')}
        </Link>
      ) : null}
      {item.reason === 'before_first_period' ? (
        <>
          <span className="text-sm break-words text-muted-foreground">
            {t('payroll.unresolved.payWithItemHelp')}
          </span>
          <Link
            to={`/payroll/tutors/${encodeURIComponent(item.tutor_user_id)}#${PAY_ITEMS_ANCHOR}`}
            aria-label={t('payroll.unresolved.payWithItemFor', {
              name: item.tutor_display_name,
            })}
            className={LINK_CLASS}
          >
            {t('payroll.unresolved.payWithItem')}
          </Link>
        </>
      ) : null}
    </div>
  );
}

function SessionLink({ item }: { item: UnresolvedItem }) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  return (
    <Link
      to={`/schedule/sessions/${encodeURIComponent(item.class_session_id)}`}
      aria-label={t('payroll.unresolved.openFor', {
        name: item.tutor_display_name,
        date: formatLocalDate(item.service_date),
      })}
      className={LINK_CLASS}
    >
      {t('payroll.unresolved.open')}
    </Link>
  );
}

function useItemText() {
  const { t } = useTranslation();
  const labels = usePayLabels();
  const { formatLocalDate } = useFormatters();
  return (item: UnresolvedItem) => ({
    date: formatLocalDate(item.service_date),
    kind: labels.kind(item.class_kind),
    source: t(`payroll.unresolved.sources.${item.source}`),
    blocking: item.blocking
      ? t('payroll.unresolved.blocks')
      : t('payroll.unresolved.doesNotBlock'),
  });
}

function Items({
  items,
  canOpenSession,
}: {
  items: readonly UnresolvedItem[];
  canOpenSession: boolean;
}) {
  const { t } = useTranslation();
  const text = useItemText();
  const key = (item: UnresolvedItem) =>
    `${item.class_session_id}:${item.tutor_user_id}`;
  return (
    <>
      <div className="hidden md:block" data-unresolved-table>
        <Table>
          <TableCaption className="sr-only">
            {t('payroll.unresolved.caption')}
          </TableCaption>
          <TableHeader>
            <TableRow>
              {(
                [
                  'date',
                  'tutor',
                  'kind',
                  'source',
                  'reason',
                  'blocking',
                ] as const
              ).map((column) => (
                <TableHead key={column} scope="col">
                  {t(`payroll.unresolved.columns.${column}`)}
                </TableHead>
              ))}
              {canOpenSession ? (
                <TableHead scope="col">
                  {t('payroll.unresolved.columns.session')}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const row = text(item);
              return (
                <TableRow
                  key={key(item)}
                  data-unresolved-id={item.class_session_id}
                  data-reason={item.reason}
                  className="align-top"
                >
                  <TableCell>{row.date}</TableCell>
                  <TableCell className="whitespace-normal">
                    {item.tutor_display_name}
                  </TableCell>
                  <TableCell>{row.kind}</TableCell>
                  <TableCell className="whitespace-normal">
                    {row.source}
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <Reason item={item} />
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    {row.blocking}
                  </TableCell>
                  {canOpenSession ? (
                    <TableCell>
                      <SessionLink item={item} />
                    </TableCell>
                  ) : null}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <ul className="flex flex-col gap-3 md:hidden" data-unresolved-cards>
        {items.map((item) => {
          const row = text(item);
          return (
            <li
              key={key(item)}
              data-unresolved-card={item.class_session_id}
              className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
            >
              <p className="font-medium break-words">
                {item.tutor_display_name}
              </p>
              <dl className="grid grid-cols-1 gap-1 text-sm">
                {(
                  [
                    [t('payroll.unresolved.columns.date'), row.date],
                    [t('payroll.unresolved.columns.kind'), row.kind],
                    [t('payroll.unresolved.columns.source'), row.source],
                    [t('payroll.unresolved.columns.blocking'), row.blocking],
                  ] as const
                ).map(([term, value]) => (
                  <div key={term} className="min-w-0">
                    <dt className="font-medium">{term}</dt>
                    <dd className="break-words">{value}</dd>
                  </div>
                ))}
                <div className="min-w-0">
                  <dt className="font-medium">
                    {t('payroll.unresolved.columns.reason')}
                  </dt>
                  <dd>
                    <Reason item={item} />
                  </dd>
                </div>
              </dl>
              {canOpenSession ? <SessionLink item={item} /> : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function UnresolvedWorkView({
  state,
  payroll,
  filters,
  onFilters,
  searchTutors,
  tutorName,
  onTutorName,
  canOpenSession,
}: UnresolvedWorkViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    filters: `${id}-filters`,
    reason: `${id}-reason`,
  };
  const filtered = filters.tutor !== null || filters.reason !== null;
  const off = payroll.status === 'ready' && payroll.off;
  const chipName = tutorName ?? t('payroll.unresolved.filters.selected');
  const clear = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="min-h-11 md:min-h-8"
      onClick={() => onFilters({ tutor: null, reason: null })}
    >
      {t('payroll.unresolved.filters.clear')}
    </Button>
  );

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    if (filtered) {
      content = (
        <p role="status" className="text-sm">
          {t('payroll.unresolved.emptyFiltered')}
        </p>
      );
    } else if (payroll.status === 'loading') {
      content = <LoadingState rows={1} />;
    } else if (payroll.status === 'error') {
      content = <ErrorAlert error={payroll.error} onRetry={payroll.onRetry} />;
    } else {
      content = off ? null : (
        <p role="status" className="text-sm" data-unresolved-empty>
          {t('payroll.unresolved.empty')}
        </p>
      );
    }
  } else {
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        <Items items={state.items} canOpenSession={canOpenSession} />
        {state.moreError ? (
          <ErrorAlert error={state.moreError} onRetry={state.onMore} />
        ) : state.hasMore ? (
          <div>
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              disabled={state.loadingMore}
              onClick={state.onMore}
            >
              {state.loadingMore ? (
                <Spinner label={t('payroll.unresolved.more')} showLabel />
              ) : (
                t('payroll.unresolved.more')
              )}
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('payroll.unresolved.title')}
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('payroll.unresolved.intro')}
        </p>
      </div>
      {off ? (
        <p
          className="flex flex-wrap items-center gap-x-2 text-sm"
          data-payroll-off
        >
          <span>{t('payroll.unresolved.payrollOff')}</span>
          <Link to="/payroll/settings" className={LINK_CLASS}>
            {t('payroll.unresolved.payrollOffLink')}
          </Link>
        </p>
      ) : null}
      <section
        aria-labelledby={ids.filters}
        className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
      >
        <h2 id={ids.filters} className="text-lg font-semibold">
          {t('payroll.unresolved.filters.label')}
        </h2>
        <div className="grid min-w-0 gap-4 md:grid-cols-2">
          <div className="min-w-0">
            <SearchPicker<PayrollTutor>
              label={t('payroll.unresolved.filters.tutor')}
              help={t('payroll.unresolved.filters.tutorHelp')}
              queryKey={[...UNRESOLVED_KEY, 'tutors']}
              search={searchTutors}
              optionId={(tutor) => tutor.user_id}
              optionLabel={(tutor) => tutor.display_name}
              selectedId={filters.tutor}
              onSelect={(tutor) => {
                onTutorName(tutor.user_id, tutor.display_name);
                onFilters({ tutor: tutor.user_id });
              }}
              inputClassName={INPUT_CLASS}
              optionClassName="min-h-11 md:min-h-9"
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={ids.reason} className="text-sm font-medium">
              {t('payroll.unresolved.filters.reason')}
            </label>
            <select
              id={ids.reason}
              className={SELECT_CLASS}
              value={filters.reason ?? ''}
              onChange={(event) =>
                onFilters({
                  reason:
                    UNRESOLVED_REASONS.find(
                      (reason) => reason === event.target.value,
                    ) ?? null,
                })
              }
            >
              <option value="">
                {t('payroll.unresolved.filters.allReasons')}
              </option>
              {UNRESOLVED_REASONS.map((reason) => (
                <option key={reason} value={reason}>
                  {t(`payroll.unresolved.filters.reasons.${reason}`)}
                </option>
              ))}
            </select>
          </div>
        </div>
        {filtered ? (
          <div className="flex flex-wrap items-center gap-2">
            {filters.tutor ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-auto min-h-11 whitespace-normal md:min-h-8"
                aria-label={t('payroll.unresolved.filters.remove', {
                  name: chipName,
                })}
                onClick={() => onFilters({ tutor: null })}
              >
                {t('payroll.unresolved.filters.chip', { name: chipName })}
                <X aria-hidden="true" />
              </Button>
            ) : null}
            {clear}
          </div>
        ) : null}
      </section>
      {content}
    </div>
  );
}
