import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useSession } from '@/features/auth/useSession';
import { compensationKey, fetchCompensation } from './api';
import { CompensationView } from './CompensationView';

// /my-pay (#425 F), tutors only: the signed-in tutor's own terms and rates,
// upcoming ones included, on the shared view with no actions: no add, no
// delete, no link to /payroll. A 404 means the role was lost since the
// session loaded: the forbidden page.

export function MyPayPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.myPay.title'));
  const session = useSession();
  const userId = session.status === 'signedIn' ? session.user.id : '';
  const detail = useQuery({
    queryKey: compensationKey(userId),
    queryFn: ({ signal }) => fetchCompensation(userId, signal),
    enabled: userId !== '',
  });

  if (detail.error instanceof ApiError && detail.error.status === 404) {
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
  return (
    <CompensationView
      compensation={detail.data}
      title={t('payroll.myPay.title')}
      emptyMessage={t('payroll.myPay.empty')}
    />
  );
}
