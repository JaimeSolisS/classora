import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useSession } from '@/features/auth/useSession';
import { earningsPeriodKey, fetchEarningsPeriod } from './api';
import {
  EarningsPeriodView,
  type EarningsPeriodState,
} from './EarningsPeriodView';
import { isUuid } from './periodRules';

// /my-pay/periods/:periodId (M7-10b C), tutors only: one of the signed-in
// tutor's finalized periods, read under their own path. A 404 (another
// tutor's period, one that isn't finalized, an unknown id) and an id that
// isn't a UUID are the same "not found", with a link back to My pay (A2):
// the page never tells them apart.

export function EarningsPeriodPage() {
  const { t } = useTranslation();
  const { periodId = '' } = useParams();
  const session = useSession();
  const userId = session.status === 'signedIn' ? session.user.id : '';
  const valid = isUuid(periodId);
  const query = useQuery({
    queryKey: earningsPeriodKey(userId, periodId.toLowerCase()),
    queryFn: ({ signal }) => fetchEarningsPeriod(userId, periodId, signal),
    enabled: userId !== '' && valid,
    meta: { expectedStatuses: [404] },
  });
  useDocumentTitle(t('payroll.earningsPeriod.fallbackTitle'));

  const notFound =
    !valid || (query.error instanceof ApiError && query.error.status === 404);
  const state: EarningsPeriodState = notFound
    ? { status: 'notFound' }
    : query.data
      ? {
          status: 'ready',
          detail: query.data,
          refreshError: query.isError ? query.error : null,
          onRefresh: () => void query.refetch(),
        }
      : query.isError
        ? {
            status: 'error',
            error: query.error,
            onRetry: () => void query.refetch(),
          }
        : { status: 'loading' };

  return <EarningsPeriodView state={state} tutorUserId={userId} />;
}
