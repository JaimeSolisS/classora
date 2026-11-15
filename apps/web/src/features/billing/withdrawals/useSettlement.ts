import { useQueries } from '@tanstack/react-query';
import { useCan } from '@/features/auth/useSession';
import {
  fetchSubjectWithdrawal,
  subjectWithdrawalKey,
  type SubjectKind,
} from './api';

// The roster side of a withdrawal settlement (#387 B1, B2).

/**
 * Whether the roster's Withdraw becomes the two-step withdrawal with its
 * settlement, and withdrawn rows get "Settle billing": schedule.manage
 * (the confirm withdraws the enrollment) and billing.view (the preview),
 * plus an outcome to confirm (billing.manage or finance.refund.approve).
 * Anyone else with schedule.manage (a Coordinator) keeps the plain M4
 * withdrawal (#387 D2).
 */
export function useCanSettleEnrollments(): boolean {
  const canManageSchedule = useCan('schedule.manage');
  const canView = useCan('billing.view');
  const canCredit = useCan('billing.manage');
  const canRefund = useCan('finance.refund.approve');
  return canManageSchedule && canView && (canCredit || canRefund);
}

/**
 * The withdrawn enrollments with no withdrawal record yet (S3, settle
 * only): one `GET /api/v1/billing/withdrawals?<kind>_id=…&limit=1` per
 * withdrawn row, cached, so a roster render sends at most one request per
 * row and a re-render none (the API has no filter by group or cohort).
 * Nothing is sent when `enabled` is false.
 */
export function useUnsettledEnrollments(
  kind: Extract<SubjectKind, 'group_enrollment' | 'cohort_enrollment'>,
  ids: readonly string[],
  enabled: boolean,
): ReadonlySet<string> {
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: subjectWithdrawalKey({ kind, id }),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchSubjectWithdrawal({ kind, id }, signal),
      enabled,
      staleTime: 60_000,
      retry: false,
    })),
  });
  return new Set(ids.filter((_, index) => results[index]?.data === null));
}
