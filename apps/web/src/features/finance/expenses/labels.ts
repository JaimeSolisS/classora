import { useAccountLabels } from '@/features/billing/accounts/labels';
import type { ExpensePayment } from '../api';

/** An expense payment in words (M7-12b C2, D2): date, amount, method and
 * reference, the billing labels for the method. */
export function usePaymentSummary() {
  const labels = useAccountLabels();
  return (payment: ExpensePayment) =>
    [
      labels.date(payment.paid_on),
      labels.money(payment.amount),
      [labels.method(payment.method_code), payment.reference]
        .filter(Boolean)
        .join(' '),
    ].join(', ');
}
