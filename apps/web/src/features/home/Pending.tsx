import {
  useQueries,
  useQuery,
  type UseQueryResult,
} from '@tanstack/react-query';
import { CircleAlert } from 'lucide-react';
import type { ParseKeys } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { familyBillingPath } from '@/features/family-portal/billing/access';
import {
  fetchOwnFamily,
  fetchSummary,
  OWN_FAMILY_KEY,
  portalKeys,
} from '@/features/family-portal/billing/api';
import {
  fetchOverdueCloseouts,
  OVERDUE_CLOSEOUTS_KEY,
} from '@/features/scheduling/overdue/api';
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import {
  fetchHomeworkSummary,
  fetchOpenApprovalsTotal,
  fetchOverdueAccountsTotal,
  fetchPendingOffersTotal,
  fetchToGrade,
  homeKeys,
} from './api';
import { HomeCard, LINK_CLASS, NOTE_CLASS, type HeadingLevel } from './Card';
import {
  readableStudents,
  useFamilyStudents,
  useStaffSessions,
  useTutorSchedule,
} from './queries';
import {
  MAX_FAMILY_STUDENTS,
  staffAgenda,
  toCloseOut,
  type PendingCode,
  type Window,
} from './rules';

// The pending items of Home (HF-06 C1–C4): counts with a labelled link to
// the page where the work is done, each read only when its access passes
// (`plan.pending`). An item hides at 0, and on a 403 or a 404 (C2: no
// alert, no retry loop); any other error shows a compact line with "Try
// again" on that item only. The "needs attention" and "to close out" counts
// come from the agenda's own response (one request); when that read fails,
// its agenda section shows the error and the item hides.

type ItemState =
  | { status: 'loading' }
  | { status: 'hidden' }
  | { status: 'error'; retry: () => void }
  | { status: 'shown'; text: string; to: string };

interface Item {
  code: PendingCode;
  state: ItemState;
}

function isRefused(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 403 || error.status === 404)
  );
}

/** The count's text and link, or hidden at 0. */
function counted(
  query: UseQueryResult<{ count: number; more: boolean }>,
  show: (count: number, shown: string) => { text: string; to: string },
): ItemState {
  if (query.isPending) {
    return { status: 'loading' };
  }
  if (query.isError) {
    return isRefused(query.error)
      ? { status: 'hidden' }
      : { status: 'error', retry: () => void query.refetch() };
  }
  const { count, more } = query.data;
  if (count === 0) {
    return { status: 'hidden' };
  }
  return { status: 'shown', ...show(count, more ? `${count}+` : `${count}`) };
}

const total = (value: number) => ({ count: value, more: false });

const ERROR_LABELS: Record<PendingCode, ParseKeys<'common'>> = {
  approvals: 'home.pending.errorLabels.approvals',
  overdueCloseouts: 'home.pending.errorLabels.overdueCloseouts',
  overdueAccounts: 'home.pending.errorLabels.overdueAccounts',
  attention: 'home.pending.errorLabels.attention',
  offers: 'home.pending.errorLabels.offers',
  toCloseOut: 'home.pending.errorLabels.toCloseOut',
  toGrade: 'home.pending.errorLabels.toGrade',
  homeworkPending: 'home.pending.errorLabels.homework',
  homeworkMissing: 'home.pending.errorLabels.homework',
  balance: 'home.pending.errorLabels.balance',
};

export function PendingSection({
  codes,
  userId,
  today,
  timeZone,
  staffWindow,
  personalWindow,
  now,
  level,
}: {
  codes: readonly PendingCode[];
  userId: string;
  today: LocalDate;
  timeZone: string;
  staffWindow: Window;
  personalWindow: Window;
  now: number;
  level: HeadingLevel;
}) {
  const { t } = useTranslation();
  const { formatMoney } = useFormatters();
  const has = (code: PendingCode) => codes.includes(code);

  const approvals = useQuery({
    queryKey: homeKeys.approvals,
    queryFn: async ({ signal }) => total(await fetchOpenApprovalsTotal(signal)),
    enabled: has('approvals'),
  });
  // The Schedule page's own count read (#301b B): the same key.
  const closeouts = useQuery({
    queryKey: [...OVERDUE_CLOSEOUTS_KEY, 'count'],
    queryFn: ({ signal }) =>
      fetchOverdueCloseouts({ limit: 1, offset: 0 }, signal),
    select: (page) => total(page.total),
    enabled: has('overdueCloseouts'),
    retry: false,
  });
  const accounts = useQuery({
    queryKey: homeKeys.overdueAccounts,
    queryFn: async ({ signal }) =>
      total(await fetchOverdueAccountsTotal(signal)),
    enabled: has('overdueAccounts'),
  });
  const sessions = useStaffSessions(staffWindow, has('attention'));
  const offers = useQuery({
    queryKey: homeKeys.offers,
    queryFn: async ({ signal }) => total(await fetchPendingOffersTotal(signal)),
    enabled: has('offers'),
  });
  const schedule = useTutorSchedule(userId, personalWindow, has('toCloseOut'));
  const toGrade = useQuery({
    queryKey: homeKeys.toGrade,
    queryFn: ({ signal }) => fetchToGrade(signal),
    enabled: has('toGrade'),
  });
  const family = useFamilyStudents(has('homeworkPending'));
  const students = readableStudents(
    family.data?.items ?? [],
    MAX_FAMILY_STUDENTS,
  );
  const homework = useQueries({
    queries: students.map((student) => ({
      queryKey: homeKeys.studentHomework(student.student_id),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchHomeworkSummary(student.student_id, signal),
    })),
  });
  const own = useQuery({
    queryKey: OWN_FAMILY_KEY,
    queryFn: ({ signal }) => fetchOwnFamily(signal),
    enabled: has('balance'),
    retry: false,
  });
  const familyId = own.data?.family?.id ?? '';
  const balance = useQuery({
    queryKey: portalKeys.summary(familyId),
    queryFn: ({ signal }) => fetchSummary(familyId, signal),
    enabled: has('balance') && familyId !== '',
    retry: false,
  });

  function homeworkState(field: 'pending' | 'missing'): ItemState {
    if (family.isPending || homework.some((query) => query.isPending)) {
      return family.isError ? { status: 'hidden' } : { status: 'loading' };
    }
    if (family.isError) {
      // The family agenda shows this read's error.
      return { status: 'hidden' };
    }
    const failed = homework.filter(
      (query) => query.isError && !isRefused(query.error),
    );
    if (failed.length > 0) {
      return {
        status: 'error',
        retry: () => failed.forEach((query) => void query.refetch()),
      };
    }
    const count = homework.reduce(
      (sum, query) => sum + (query.data?.[field] ?? 0),
      0,
    );
    if (count === 0) {
      return { status: 'hidden' };
    }
    return {
      status: 'shown',
      text: t(
        field === 'pending'
          ? 'home.pending.homeworkPending'
          : 'home.pending.homeworkMissing',
        { count, shown: `${count}` },
      ),
      to: '/my-homework',
    };
  }

  function balanceState(): ItemState {
    if (own.isPending) {
      return { status: 'loading' };
    }
    if (own.isError) {
      return isRefused(own.error)
        ? { status: 'hidden' }
        : { status: 'error', retry: () => void own.refetch() };
    }
    if (familyId === '') {
      return { status: 'hidden' };
    }
    if (balance.isPending) {
      return { status: 'loading' };
    }
    if (balance.isError) {
      return isRefused(balance.error)
        ? { status: 'hidden' }
        : { status: 'error', retry: () => void balance.refetch() };
    }
    const { overdue, outstanding } = balance.data;
    if (Number(overdue) > 0) {
      return {
        status: 'shown',
        text: t('home.pending.balanceOverdue', {
          amount: formatMoney(overdue),
        }),
        to: familyBillingPath('overdue'),
      };
    }
    if (Number(outstanding) > 0) {
      return {
        status: 'shown',
        text: t('home.pending.balanceDue', {
          amount: formatMoney(outstanding),
        }),
        to: familyBillingPath(),
      };
    }
    return { status: 'hidden' };
  }

  function attentionState(): ItemState {
    if (sessions.isPending) {
      return { status: 'loading' };
    }
    if (sessions.isError) {
      return { status: 'hidden' };
    }
    const count = staffAgenda(sessions.data.items, today, timeZone).attention;
    if (count === 0) {
      return { status: 'hidden' };
    }
    const shown = sessions.data.truncated ? `${count}+` : `${count}`;
    return {
      status: 'shown',
      text: t('home.pending.attention', { count, shown }),
      to: `/schedule?view=list&date=${today}`,
    };
  }

  function closeOutState(): ItemState {
    if (schedule.isPending) {
      return { status: 'loading' };
    }
    if (schedule.isError) {
      return { status: 'hidden' };
    }
    const ended = toCloseOut(schedule.data.entries, now);
    if (ended.length === 0) {
      return { status: 'hidden' };
    }
    const count = ended.length;
    return {
      status: 'shown',
      text: t('home.pending.toCloseOut', { count, shown: `${count}` }),
      to:
        count === 1
          ? `/my-schedule/sessions/${ended[0]!.item.id}/closeout`
          : '/my-schedule',
    };
  }

  const states: Record<PendingCode, () => ItemState> = {
    approvals: () =>
      counted(approvals, (count, shown) => ({
        text: t('home.pending.approvals', { count, shown }),
        to: '/approvals',
      })),
    overdueCloseouts: () =>
      counted(closeouts, (count, shown) => ({
        text: t('home.pending.overdueCloseouts', { count, shown }),
        to: '/schedule/overdue-closeouts',
      })),
    overdueAccounts: () =>
      counted(accounts, (count, shown) => ({
        text: t('home.pending.overdueAccounts', { count, shown }),
        to: '/billing/overdue',
      })),
    attention: attentionState,
    offers: () =>
      counted(offers, (count, shown) => ({
        text: t('home.pending.offers', { count, shown }),
        to: '/offers',
      })),
    toCloseOut: closeOutState,
    toGrade: () =>
      counted(toGrade, (count, shown) => ({
        text: t('home.pending.toGrade', { count, shown }),
        to: '/assignments',
      })),
    homeworkPending: () => homeworkState('pending'),
    homeworkMissing: () => homeworkState('missing'),
    balance: balanceState,
  };

  const items: Item[] = codes.map((code) => ({ code, state: states[code]() }));
  const loading = items.some((item) => item.state.status === 'loading');
  const visible = items.filter(
    (item) => item.state.status === 'shown' || item.state.status === 'error',
  );

  return (
    <HomeCard
      title={t('home.pending.title')}
      level={level}
      data-home-section="pending"
    >
      {visible.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {visible.map(({ code, state }) => (
            <li key={code} data-home-pending={code}>
              {state.status === 'shown' ? (
                <Link to={state.to} className={LINK_CLASS}>
                  {state.text}
                </Link>
              ) : state.status === 'error' ? (
                <span className="flex flex-wrap items-center gap-2 text-sm">
                  <CircleAlert
                    aria-hidden="true"
                    className="size-4 shrink-0 text-destructive"
                  />
                  {t('home.pending.itemError', {
                    label: t(ERROR_LABELS[code]),
                  })}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="min-h-11 md:min-h-8"
                    onClick={state.retry}
                  >
                    {t('feedback.retry')}
                  </Button>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {loading ? (
        <LoadingState rows={2} label={t('home.pending.loading')} />
      ) : visible.length === 0 ? (
        <p className={NOTE_CLASS} data-home-nothing>
          {t('home.pending.nothing')}
        </p>
      ) : null}
    </HomeCard>
  );
}
