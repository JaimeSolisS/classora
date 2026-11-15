import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import {
  fetchPeriodReview,
  PAYROLL_PERIODS_KEY,
  periodReviewKey,
  postPayrollAdjustment,
  UNRESOLVED_KEY,
  type PayrollAdjustmentCreate,
} from './api';
import { REFRESH_AFTER_ADJUSTMENT } from './adjustmentForm';
import { isCode, isUnknownResult } from './periodRules';

// Posting a manual adjustment from any of its three entry points (M7-08b B4):
// after a stored one, or a refusal or lost answer that may mean the lists
// moved, the periods (list and reviews) and the Unresolved list refetch. A
// success resolves with the announcement naming the target period's dates,
// read from that period; if that read fails, the announcement says only that
// it was saved. No amount is announced (E2).

export function useManualAdjustment() {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const queryClient = useQueryClient();

  async function refresh(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: PAYROLL_PERIODS_KEY }),
      queryClient.invalidateQueries({ queryKey: UNRESOLVED_KEY }),
    ]);
  }

  return async function post(
    tutorUserId: string,
    body: PayrollAdjustmentCreate,
    key: string,
  ): Promise<string> {
    let targetId: string;
    try {
      targetId = (await postPayrollAdjustment(tutorUserId, body, key))
        .target_period_id;
    } catch (caught) {
      if (
        !isCode(caught, 'reauth_required') &&
        (isUnknownResult(caught) || isCode(caught, ...REFRESH_AFTER_ADJUSTMENT))
      ) {
        await refresh();
      }
      throw caught;
    }
    await refresh();
    try {
      const target = await queryClient.fetchQuery({
        queryKey: periodReviewKey(targetId),
        queryFn: ({ signal }) => fetchPeriodReview(targetId, signal),
      });
      return t('payroll.adjustmentDialog.saved', {
        start: formatLocalDate(target.period.starts_on),
        end: formatLocalDate(target.period.ends_on),
      });
    } catch {
      return t('payroll.adjustmentDialog.savedNoDates');
    }
  };
}
