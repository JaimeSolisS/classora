import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan, useSession } from '@/features/auth/useSession';
import {
  fetchPayrollSettings,
  fetchUnresolved,
  PAYROLL_SETTINGS_KEY,
  searchPayrollTutors,
  unresolvedKey,
  type UnresolvedFilters,
  type UnresolvedItem,
} from './api';
import { ManualAdjustmentDialog } from './ManualAdjustmentDialog';
import { PayrollTabs } from './PayrollTabs';
import { useManualAdjustment } from './useManualAdjustment';
import {
  parseUnresolvedFilters,
  unresolvedSearchParams,
} from './unresolvedUrl';
import {
  UnresolvedWorkView,
  type PayrollOffState,
  type UnresolvedState,
} from './UnresolvedWorkView';

// /payroll/unresolved (#444 C), for payroll.view holders. The filters live in
// the URL; "Load more" follows the API's cursor, so the pages stay in memory
// and a filter starts over. The picker's typing is its own debounced state
// and never reaches the URL. A URL write is composed from what the user last
// asked for, never from rendered params: a navigation can still be pending,
// so two quick picks would otherwise lose the first (QA #425 D6). A committed
// URL that isn't one of our own writes (back/forward, a link) replaces the
// intent. The tutor chip names the picked tutor, else the first row's, else
// "Selected tutor".

export function UnresolvedWorkPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.unresolved.title'));
  const canOpenSession = useCan('schedule.view');
  const canManage = useCan('payroll.manage');
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const postAdjustment = useManualAdjustment();
  const [paying, setPaying] = useState<UnresolvedItem | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSearch = searchParams.toString();
  const filters = useMemo(
    () => parseUnresolvedFilters(new URLSearchParams(urlSearch)),
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
    intent.current = parseUnresolvedFilters(new URLSearchParams(urlSearch));
  }, [urlSearch]);

  function changeFilters(next: Partial<UnresolvedFilters>) {
    intent.current = { ...intent.current, ...next };
    const params = unresolvedSearchParams(intent.current);
    const value = params.toString();
    if (value !== (written.current.at(-1) ?? committed.current)) {
      written.current.push(value);
      setSearchParams(params);
    }
  }

  const list = useInfiniteQuery({
    queryKey: unresolvedKey(filters),
    queryFn: ({ pageParam, signal }) =>
      fetchUnresolved(filters, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const settings = useQuery({
    queryKey: PAYROLL_SETTINGS_KEY,
    queryFn: ({ signal }) => fetchPayrollSettings(signal),
  });

  const items = useMemo(
    () => list.data?.pages.flatMap((page) => page.items) ?? [],
    [list.data],
  );
  const state: UnresolvedState = list.data
    ? {
        status: 'ready',
        items,
        hasMore: list.hasNextPage,
        loadingMore: list.isFetchingNextPage,
        moreError: list.isFetchNextPageError ? list.error : null,
        onMore: () => void list.fetchNextPage(),
        // A failed refetch keeps the rows: they may be out of date (M7-08b).
        refreshError: list.isRefetchError ? list.error : null,
        onReload: () => void list.refetch(),
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  const payroll: PayrollOffState = settings.data
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
      items.find((item) => item.tutor_user_id === filters.tutor)
        ?.tutor_display_name ??
      null)
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PayrollTabs />
      <UnresolvedWorkView
        state={state}
        payroll={payroll}
        filters={filters}
        onFilters={changeFilters}
        searchTutors={searchPayrollTutors}
        tutorName={tutorName}
        onTutorName={(id, name) =>
          setNames((current) => ({ ...current, [id]: name }))
        }
        canOpenSession={canOpenSession}
        // A non-Owner's own pay never (#105 E5; the API's 403 is handled).
        canPayByHand={(item) =>
          canManage &&
          (item.tutor_user_id !== user?.id || user.roles.includes('owner'))
        }
        onPayByHand={(item) => {
          setAnnouncement('');
          setPaying(item);
        }}
        busy={list.isFetching && !list.isFetchingNextPage}
      />
      <p role="status" className="text-sm" data-unresolved-announcement>
        {announcement}
      </p>
      {paying ? (
        <ManualAdjustmentDialog
          open
          tutorName={paying.tutor_display_name}
          reference={{
            kind: 'session',
            sessionId: paying.class_session_id,
            serviceDate: paying.service_date,
            reason: paying.reason,
          }}
          onSubmit={async (body, key) => {
            setAnnouncement(
              await postAdjustment(paying.tutor_user_id, body, key),
            );
          }}
          onClose={(outcome) => {
            setPaying(null);
            if (outcome === 'unknown') {
              setAnnouncement(t('payroll.adjustmentDialog.closedUnknown'));
            }
          }}
        />
      ) : null}
    </div>
  );
}
