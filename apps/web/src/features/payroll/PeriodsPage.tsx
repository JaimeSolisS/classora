import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { PAYROLL_ACCESS } from './access';
import {
  fetchPayrollPeriods,
  fetchPayrollSettings,
  PAYROLL_SETTINGS_KEY,
  periodsListKey,
  searchPayrollTutors,
  type PayrollPeriodListParams,
} from './api';
import { PayrollTabs } from './PayrollTabs';
import { parsePeriodFilters, periodSearchParams } from './periodRules';
import { PeriodsView, type PeriodsState } from './PeriodsView';
import type { PayrollOffState } from './UnresolvedWorkView';

// /payroll/periods (#110b B), for payroll.view, payroll.prepare and
// payroll.finalize holders. The filters and the page live in the URL; a
// filter goes back to page 1. The picker's typing is its own debounced state
// and never reaches the URL. A URL write is composed from what the user last
// asked for, never from rendered params: a navigation can still be pending
// (QA #425 D6, BRIEF 2026-11-01). A committed URL that isn't one of our own
// writes (back/forward, a link) replaces the intent. The settings, and the
// tutor picker over the Payroll list, are read only by payroll.view holders:
// a delegated Administrator can't read them.

export function PeriodsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.periods.title'));
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const canView = canAccess(user, { permission: 'payroll.view' });
  // Who reads open periods too; payroll.finalize alone reads prepared and
  // finalized ones (#110 E8).
  const readsOpen =
    canView || canAccess(user, { permission: 'payroll.prepare' });
  const canOpenSettings = canAccess(user, PAYROLL_ACCESS);
  const [searchParams, setSearchParams] = useSearchParams();
  // A send-back that took the period out of a reviewer's sight (QA #459 D1).
  const locationState: unknown = useLocation().state;
  const notice =
    typeof locationState === 'object' &&
    locationState !== null &&
    'periodsNotice' in locationState &&
    typeof locationState.periodsNotice === 'string'
      ? locationState.periodsNotice
      : '';
  const urlSearch = searchParams.toString();
  const filters = useMemo(
    () => parsePeriodFilters(new URLSearchParams(urlSearch)),
    [urlSearch],
  );
  const [names, setNames] = useState<Readonly<Record<string, string>>>({});

  const committed = useRef(urlSearch);
  const intent = useRef(filters);
  const written = useRef<string[]>([]);

  useEffect(() => {
    committed.current = urlSearch;
    const own = written.current.indexOf(urlSearch);
    if (own >= 0) {
      written.current.splice(0, own + 1);
      return;
    }
    written.current = [];
    intent.current = parsePeriodFilters(new URLSearchParams(urlSearch));
  }, [urlSearch]);

  function write(next: PayrollPeriodListParams) {
    intent.current = next;
    const params = periodSearchParams(next);
    const value = params.toString();
    if (value !== (written.current.at(-1) ?? committed.current)) {
      written.current.push(value);
      setSearchParams(params);
    }
  }

  /** A filter goes back to page 1. */
  function changeFilters(next: Partial<PayrollPeriodListParams>) {
    write({ ...intent.current, ...next, page: 1 });
  }

  function goToPage(page: number) {
    write({ ...intent.current, page });
  }

  const list = useQuery({
    queryKey: periodsListKey(filters),
    queryFn: ({ signal }) => fetchPayrollPeriods(filters, signal),
    placeholderData: keepPreviousData,
  });
  const settings = useQuery({
    queryKey: PAYROLL_SETTINGS_KEY,
    queryFn: ({ signal }) => fetchPayrollSettings(signal),
    enabled: canView,
  });

  // Another filter's rows are never shown as this one's, and rows whose
  // refresh failed say they may be out of date until a refresh succeeds.
  const state: PeriodsState =
    list.isError && (!list.data || list.isPlaceholderData)
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : list.data
        ? {
            status: 'ready',
            page: list.data,
            refreshError: list.isError ? list.error : null,
            onRefresh: () => void list.refetch(),
          }
        : { status: 'loading' };

  const payroll: PayrollOffState | null = !canView
    ? null
    : settings.data
      ? { status: 'ready', off: settings.data.payroll_starts_on === null }
      : settings.isError
        ? {
            status: 'error',
            error: settings.error,
            onRetry: () => void settings.refetch(),
          }
        : { status: 'loading' };

  const tutorName = filters.tutor
    ? (names[filters.tutor] ??
      list.data?.items.find((item) => item.tutor_user_id === filters.tutor)
        ?.tutor_display_name ??
      null)
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PayrollTabs />
      <p role="status" className="text-sm font-medium" data-periods-notice>
        {notice}
      </p>
      <PeriodsView
        state={state}
        payroll={payroll}
        filters={filters}
        onFilters={changeFilters}
        onPage={goToPage}
        searchTutors={canView ? searchPayrollTutors : null}
        tutorName={tutorName}
        onTutorName={(id, name) =>
          setNames((current) => ({ ...current, [id]: name }))
        }
        reviewerOnly={!readsOpen}
        canOpenSettings={canOpenSettings}
      />
    </div>
  );
}
