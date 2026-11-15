import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import {
  fetchFamilyStudents,
  MY_SCHEDULE_KEY,
  type FamilyStudent,
} from '@/features/my-schedule/api';
import {
  CHANGE_REQUESTS_KEY,
  fetchChangeRequests,
  PAGE_SIZE,
  withdrawChangeRequest,
} from './api';
import {
  ChangeRequestsView,
  type ChangeRequestsState,
} from './ChangeRequestsView';
import { listSearchParams, parseListState } from './rules';

// /change-requests, "My change requests" (#62b B), for guardians, students
// and tutors (the API lists the family requests of the students the caller
// may act for, and a tutor's own). The filters and the page are in the URL.
// A guardian's student filter uses GET /schedule/family-students (#67; the
// children whose records they may see). A withdrawal, or a stale one,
// refreshes the list and the pending checks of My schedule.

export function ChangeRequestsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('changeRequests.title'));
  const timeZone = useCenterTimeZone();
  const session = useSession();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseListState(params), [params]);
  const isGuardian =
    session.status === 'signedIn' &&
    (session.user.roles as readonly string[]).includes('guardian');

  const query = {
    status: filters.status,
    studentId: filters.studentId,
    offset: (filters.page - 1) * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...CHANGE_REQUESTS_KEY, 'list', query],
    queryFn: ({ signal }) => fetchChangeRequests(query, signal),
  });
  const family = useQuery({
    queryKey: [...MY_SCHEDULE_KEY, 'family-students'],
    queryFn: ({ signal }) => fetchFamilyStudents(signal),
    enabled: isGuardian,
  });
  const children: readonly FamilyStudent[] = useMemo(
    () =>
      (family.data?.items ?? []).filter(
        (student) =>
          student.relation === 'guardian' &&
          student.schedule_access === 'allowed',
      ),
    [family.data],
  );

  function refresh() {
    void client.invalidateQueries({ queryKey: CHANGE_REQUESTS_KEY });
  }

  let state: ChangeRequestsState;
  if (list.isError) {
    state = {
      status: 'error',
      error: list.error,
      onRetry: () => void list.refetch(),
    };
  } else if (!list.data) {
    state = { status: 'loading' };
  } else {
    state = {
      status: 'ready',
      items: list.data.items,
      total: list.data.total,
      offset: list.data.offset,
    };
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('changeRequests.title')}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('changeRequests.intro')}
        </p>
      </div>
      <ChangeRequestsView
        state={state}
        filters={filters}
        onFilters={(next) => setParams(listSearchParams(next))}
        students={children}
        timeZone={timeZone}
        onWithdraw={async (request) => {
          await withdrawChangeRequest(request);
          refresh();
        }}
        onStale={refresh}
      />
    </div>
  );
}

export default ChangeRequestsPage;
