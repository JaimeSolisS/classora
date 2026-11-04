import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useSession } from '@/features/auth/useSession';
import {
  compensationKey,
  earningsListKey,
  fetchCompensation,
  fetchEarnings,
} from './api';
import { CompensationView } from './CompensationView';
import { EarningsList, type EarningsListState } from './EarningsList';
import { parseEarningsPage } from './earningsRules';

// /my-pay (#425 F), tutors only: the signed-in tutor's own terms and rates,
// upcoming ones included, on the shared view with no actions: no add, no
// delete, no link to /payroll. Below them, their finalized periods (M7-10b
// E1, B), whose page is `?page=` in the URL so the back button returns to
// it. A 404 on either read means the role was lost since the session
// loaded: the forbidden page.

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function MyPayPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.myPay.title'));
  const session = useSession();
  const userId = session.status === 'signedIn' ? session.user.id : '';
  const [searchParams, setSearchParams] = useSearchParams();
  const page = parseEarningsPage(searchParams);
  const detail = useQuery({
    queryKey: compensationKey(userId),
    queryFn: ({ signal }) => fetchCompensation(userId, signal),
    enabled: userId !== '',
  });
  const earnings = useQuery({
    queryKey: earningsListKey(userId, page),
    queryFn: ({ signal }) => fetchEarnings(userId, page, signal),
    enabled: userId !== '',
    placeholderData: keepPreviousData,
    meta: { expectedStatuses: [404] },
  });

  if (isNotFound(detail.error) || isNotFound(earnings.error)) {
    return <ForbiddenPage />;
  }
  if (detail.isError) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">{t('payroll.myPay.title')}</h1>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }

  // Another page's rows are never shown as this one's, and rows whose
  // refresh failed say they may be out of date until a refresh succeeds.
  const state: EarningsListState =
    earnings.isError && (!earnings.data || earnings.isPlaceholderData)
      ? {
          status: 'error',
          error: earnings.error,
          onRetry: () => void earnings.refetch(),
        }
      : earnings.data
        ? {
            status: 'ready',
            page: earnings.data,
            refreshError: earnings.isError ? earnings.error : null,
            onRefresh: () => void earnings.refetch(),
          }
        : { status: 'loading' };

  return (
    <div className="flex min-w-0 flex-col gap-10">
      <CompensationView
        compensation={detail.data}
        title={t('payroll.myPay.title')}
        emptyMessage={t('payroll.myPay.empty')}
      />
      <EarningsList
        state={state}
        tutorUserId={userId}
        onPage={(next) => {
          const params = new URLSearchParams();
          if (next > 1) params.set('page', String(next));
          setSearchParams(params);
        }}
      />
    </div>
  );
}
