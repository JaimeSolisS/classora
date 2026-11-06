import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { canAccess } from '@/features/auth/access';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useSession } from '@/features/auth/useSession';
import {
  BALANCES_KEY,
  fetchBalances,
  fetchProfitability,
  profitabilityKey,
  type Balances,
  type Profitability,
} from './api';
import { FinancialReportView } from './FinancialReportView';
import { isRangeRefusal, refusedField, stateOf } from './reportState';
import { activePreset } from './rules';
import { useReportRange } from './useReportRange';

// /reports/finance (M7-14b), for finance.report.view holders. The basis and
// the range live in the URL (`basis`, `from`, `to`; E2). A preset or the
// basis switch writes at once; a typed date waits 400 ms, and the debounced
// write reads the current params when it fires (useEffectEvent, BRIEF
// 2026-11-01), so it keeps a basis switched meanwhile. "Today" is the
// center's: the session's zone until the API answers, then the response's
// `today`, which re-anchors the default range and the presets. A broken
// bound asks nothing and shows next to its field; so does the server's
// `422 report_range_invalid`. A failed refetch keeps the figures with the
// out-of-date alert and never says it reloaded (B6, #458).

export function FinancialReportPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('reports.title'));
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const {
    today,
    params,
    draft,
    draftError,
    asked,
    onDate,
    onBasis,
    onPreset,
    latchToday,
  } = useReportRange();

  const request = { basis: params.basis, from: params.from, to: params.to };
  const profitability = useQuery({
    queryKey: profitabilityKey(request),
    queryFn: ({ signal }) => fetchProfitability(request, signal),
    enabled: asked,
    meta: { expectedStatuses: [403, 422] },
  });
  const balances = useQuery({
    queryKey: BALANCES_KEY,
    queryFn: ({ signal }) => fetchBalances(signal),
    meta: { expectedStatuses: [403] },
  });

  // Latch the API's today (balances first: it doesn't depend on the range).
  latchToday(balances.data?.today ?? profitability.data?.today);

  const forbidden = [profitability.error, balances.error].some(
    (error) => error instanceof ApiError && error.status === 403,
  );
  if (forbidden) {
    return <ForbiddenPage />;
  }

  const refusal =
    asked && isRangeRefusal(profitability.error) ? profitability.error : null;
  const error = draftError
    ? {
        field: draftError.field,
        message: t(`reports.profitability.range.errors.${draftError.problem}`),
      }
    : refusal
      ? {
          field: refusedField(refusal),
          message: t('reports.profitability.range.errors.server'),
        }
      : null;

  return (
    <FinancialReportView
      controls={{
        basis: params.basis,
        onBasis,
        draft,
        onDate,
        preset: activePreset(params, today),
        onPreset,
        today,
        error,
      }}
      profitability={stateOf<Profitability>(
        profitability,
        !asked || (refusal !== null && profitability.data === undefined),
      )}
      balances={stateOf<Balances>(balances)}
      links={{
        overdueAccounts: canAccess(user, { permission: 'billing.view' }),
        overdueExpenses: canAccess(user, { permission: 'expense.view' }),
      }}
    />
  );
}
