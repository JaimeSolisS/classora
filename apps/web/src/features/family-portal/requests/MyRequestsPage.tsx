import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  fetchSessionRequests,
  REQUESTS_KEY,
  SLOTS_KEY,
  withdrawSessionRequest,
} from './api';
import { MyRequestsView, type MyRequestsState } from './MyRequestsView';

// /requests, "My session requests" (#59 F4), for guardians and students
// (the API lists the requests of the students the caller may act for).
// A withdrawal, or a stale one, refreshes the list (and the slots).

export function MyRequestsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('sessionRequests.list.title'));
  const timeZone = useCenterTimeZone();
  const client = useQueryClient();
  const list = useQuery({
    queryKey: REQUESTS_KEY,
    queryFn: ({ signal }) => fetchSessionRequests(signal),
  });

  function refresh() {
    void client.invalidateQueries({ queryKey: REQUESTS_KEY });
    void client.invalidateQueries({ queryKey: SLOTS_KEY });
  }

  let state: MyRequestsState;
  if (list.isError) {
    state = {
      status: 'error',
      error: list.error,
      onRetry: () => void list.refetch(),
    };
  } else if (!list.data) {
    state = { status: 'loading' };
  } else {
    state = { status: 'ready', items: list.data.items };
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('sessionRequests.list.title')}
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('sessionRequests.list.intro')}
        </p>
        <Link
          to="/requests/new"
          className="w-fit text-sm font-medium underline underline-offset-4"
        >
          {t('sessionRequests.list.requestOne')}
        </Link>
      </div>
      <MyRequestsView
        state={state}
        timeZone={timeZone}
        onWithdraw={async (request) => {
          await withdrawSessionRequest(request);
          refresh();
        }}
        onStale={refresh}
      />
    </div>
  );
}
