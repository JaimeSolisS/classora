import { useQuery } from '@tanstack/react-query';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { canAccess } from '@/features/auth/access';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import {
  BALANCES_KEY,
  fetchBalances,
  fetchProfitability,
  profitabilityKey,
  type Balances,
  type Profitability,
  type ReportBasis,
} from './api';
import { FinancialReportView } from './FinancialReportView';
import type { ReportState } from './parts';
import {
  activePreset,
  isLocalDate,
  presetRange,
  rangeError,
  reportParams,
  type DateRange,
  type Preset,
  type RangeField,
} from './rules';

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

const DATE_DEBOUNCE_MS = 400;
const RANGE_INVALID = 'report_range_invalid';

function isRangeRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === RANGE_INVALID;
}

/** The field a `422 report_range_invalid` names (`to` if it names none). */
function refusedField(error: ApiError): RangeField {
  const field = error.fieldErrors.find((item) => item.code === RANGE_INVALID)
    ?.path[1];
  return field === 'from' ? 'from' : 'to';
}

function stateOf<T>(
  query: {
    data: T | undefined;
    isError: boolean;
    error: unknown;
    isFetching: boolean;
    refetch: () => unknown;
  },
  idle = false,
): ReportState<T> {
  if (idle) return { status: 'idle' };
  if (query.data !== undefined) {
    return {
      status: 'ready',
      data: query.data,
      refreshError: query.isError ? query.error : null,
      refreshing: query.isFetching,
      onRefresh: () => void query.refetch(),
    };
  }
  if (query.isError) {
    return {
      status: 'error',
      error: query.error,
      onRetry: () => void query.refetch(),
    };
  }
  return { status: 'loading' };
}

export function FinancialReportPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('reports.title'));
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const timeZone = useCenterTimeZone();
  const [searchParams, setSearchParams] = useSearchParams();

  // The center's today: the API's once it has answered (E2).
  const [apiToday, setApiToday] = useState<string | null>(null);
  const today = apiToday ?? todayInZone(timeZone);
  const params = reportParams(searchParams, today);

  // The dates as typed; they follow the URL whenever its range changes.
  const urlRange = `${params.from}/${params.to}`;
  const [draft, setDraft] = useState<DateRange>({
    from: params.from,
    to: params.to,
  });
  const [seenRange, setSeenRange] = useState(urlRange);
  if (seenRange !== urlRange) {
    setSeenRange(urlRange);
    setDraft({ from: params.from, to: params.to });
  }

  const [dateWait, setDateWait] = useState<number | null>(null);
  const keystrokes = useRef(0);

  const writeDates = useEffectEvent(() => {
    setDateWait(null);
    if (!isLocalDate(draft.from) || !isLocalDate(draft.to)) return;
    const next = new URLSearchParams(searchParams);
    next.set('from', draft.from);
    next.set('to', draft.to);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  });

  useEffect(() => {
    if (dateWait === null) return;
    const timer = window.setTimeout(() => writeDates(), DATE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [dateWait]);

  function onDate(field: RangeField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
    keystrokes.current += 1;
    setDateWait(keystrokes.current);
  }

  function onBasis(basis: ReportBasis) {
    const next = new URLSearchParams(searchParams);
    next.set('basis', basis);
    setSearchParams(next);
  }

  function onPreset(preset: Preset) {
    setDateWait(null);
    const range = presetRange(preset, today);
    setDraft(range);
    const next = new URLSearchParams(searchParams);
    next.set('from', range.from);
    next.set('to', range.to);
    setSearchParams(next);
  }

  const draftError = rangeError(draft, today);
  const urlError = rangeError(params, today);
  const asked = draftError === null && urlError === null;

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
  const reported = balances.data?.today ?? profitability.data?.today ?? null;
  if (reported !== null && reported !== apiToday) {
    setApiToday(reported);
  }

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
