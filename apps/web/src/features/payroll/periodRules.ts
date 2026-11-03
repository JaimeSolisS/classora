import { ApiError, CLIENT_ERROR_CODES } from '@/app/api';
import type { LocalDate } from '@/lib/time';
import type {
  LineKind,
  PayrollLine,
  PayrollPeriodDetail,
  PayrollPeriodListParams,
  PeriodStatus,
} from './api';

// The period pages' client rules (#110b), without React: the list's URL
// parameters, which actions show and why one is disabled, the line kinds,
// and the readers of the refusals' `details`. The API stays authoritative:
// these only say before the click what the server would refuse, and no
// amount is ever computed here (C3). A sign is read from the API's string.

export const PERIOD_STATUSES: readonly PeriodStatus[] = [
  'open',
  'prepared',
  'finalized',
];

/** The statuses a delegated Administrator can read (#110 E8). */
export const REVIEWER_STATUSES: readonly PeriodStatus[] = [
  'prepared',
  'finalized',
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Whether `value` is a UUID, as the API's path and query ids are. */
export function isUuid(value: string): boolean {
  return UUID.test(value);
}

// ------------------------------------------------------------- the list

/** The list's filters in the URL (B2); anything invalid is ignored. */
export function parsePeriodFilters(
  params: URLSearchParams,
): PayrollPeriodListParams {
  const status = params.get('status');
  const tutor = params.get('tutor');
  const page = Number.parseInt(params.get('page') ?? '', 10);
  return {
    status: PERIOD_STATUSES.find((item) => item === status) ?? null,
    tutor: tutor !== null && isUuid(tutor) ? tutor.toLowerCase() : null,
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

export function periodSearchParams({
  status,
  tutor,
  page,
}: PayrollPeriodListParams): URLSearchParams {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (tutor) params.set('tutor', tutor);
  if (page > 1) params.set('page', String(page));
  return params;
}

// ------------------------------------------------------------ amounts

const NEGATIVE = /^-(?!0+(\.0+)?$)/;

/** Whether an API decimal string is below zero ("-0.00" isn't). */
export function isNegativeAmount(amount: string): boolean {
  return NEGATIVE.test(amount);
}

// -------------------------------------------------------------- lines

/** The line kinds this page labels. The API sends `adjustment` since #111;
 * M7-08b labels it, so until then it reads as `unknown` (C9). */
type LabelledLineKind = Exclude<LineKind, 'adjustment'>;

export const KNOWN_LINE_KINDS: readonly LabelledLineKind[] = [
  'session',
  'late_cancellation',
  'no_show',
  'makeup',
  'salary',
  'pay_item',
];

export type LineKindKey = LabelledLineKind | 'unknown';

/** The label key of a line's kind; an unknown kind is `unknown`. */
export function lineKindKey(kind: string): LineKindKey {
  return KNOWN_LINE_KINDS.find((item) => item === kind) ?? 'unknown';
}

const SESSION_KINDS: readonly string[] = [
  'session',
  'late_cancellation',
  'no_show',
  'makeup',
];

/** A line priced from a session (C4). */
export function isSessionLine(line: Pick<PayrollLine, 'kind'>): boolean {
  return SESSION_KINDS.includes(line.kind);
}

/** A session line with no rate snapshot: covered by the salary (C4). */
export function isCoveredBySalary(
  line: Pick<PayrollLine, 'kind' | 'rate' | 'rate_model'>,
): boolean {
  return isSessionLine(line) && line.rate === null && line.rate_model === null;
}

// ------------------------------------------------------------ actions

export interface PeriodPermissions {
  /** `payroll.prepare`: recalculate, prepare and return. */
  prepare: boolean;
  /** `payroll.finalize`: return and finalize. */
  finalize: boolean;
}

export interface PeriodActions {
  recalculate: boolean;
  prepare: boolean;
  return: boolean;
  finalize: boolean;
}

/** Which actions show for the period's status and the user (Access, D14). */
export function periodActions(
  status: PeriodStatus,
  can: PeriodPermissions,
): PeriodActions {
  return {
    recalculate: status === 'open' && can.prepare,
    prepare: status === 'open' && can.prepare,
    return: status === 'prepared' && (can.prepare || can.finalize),
    finalize: status === 'prepared' && can.finalize,
  };
}

/**
 * Whether the period has ended for the center (D2): `prepare` needs `ends_on`
 * before the center's today, never the browser's.
 */
export function hasEnded(
  period: Pick<PayrollPeriodDetail, 'ends_on'>,
  today: LocalDate,
): boolean {
  return period.ends_on < today;
}

export type FinalizeBlock =
  | { kind: 'preparer' }
  | { kind: 'ownPay' }
  | { kind: 'earlier'; periodId: string };

export interface ReviewerIdentity {
  id: string;
  roles: readonly string[];
}

/**
 * Why Finalize is disabled before the click, in the API's order (D10, D11):
 * the latest preparer and the period's tutor can't finalize, unless the user
 * holds the Owner role; an earlier period of the tutor comes first.
 */
export function finalizeBlocks(
  period: Pick<
    PayrollPeriodDetail,
    'prepared_by' | 'tutor_user_id' | 'earlier_unfinalized_period_id'
  >,
  user: ReviewerIdentity,
): FinalizeBlock[] {
  const blocks: FinalizeBlock[] = [];
  const owner = user.roles.includes('owner');
  if (!owner && period.prepared_by?.id === user.id) {
    blocks.push({ kind: 'preparer' });
  }
  if (!owner && period.tutor_user_id === user.id) {
    blocks.push({ kind: 'ownPay' });
  }
  if (period.earlier_unfinalized_period_id) {
    blocks.push({
      kind: 'earlier',
      periodId: period.earlier_unfinalized_period_id,
    });
  }
  return blocks;
}

/** The return reason's limit, in characters once trimmed (#110 C1). */
export const RETURN_REASON_MAX = 1000;

/**
 * The return reason's error key (D5), or undefined: required, and at most
 * 1000 characters once trimmed, counted by code point as the API counts.
 */
export function returnReasonError(reason: string): string | undefined {
  const trimmed = reason.trim();
  if (trimmed === '') return 'payroll.returnDialog.required';
  if ([...trimmed].length > RETURN_REASON_MAX) {
    return 'payroll.returnDialog.tooLong';
  }
  return undefined;
}

// ----------------------------------------------------------- refusals

export function isCode(error: unknown, ...codes: string[]): boolean {
  return error instanceof ApiError && codes.includes(error.code);
}

function details(error: unknown): Record<string, unknown> {
  if (!(error instanceof ApiError)) return {};
  const value: unknown = error.problem?.details;
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function detailString(error: unknown, name: string): string | null {
  const value = details(error)[name];
  return typeof value === 'string' && value !== '' ? value : null;
}

/**
 * The answer may never have arrived, or the server failed: the action may
 * have happened (#454 D2's lesson). A 4xx is a settled refusal.
 */
export function isUnknownResult(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return (
    error.status === undefined ||
    error.status >= 500 ||
    error.code === CLIENT_ERROR_CODES.network ||
    error.code === CLIENT_ERROR_CODES.unexpectedResponse
  );
}

/** The refusals of `prepare` after which the page runs `recalculate` (D3). */
export const RECALCULATE_AFTER_PREPARE: readonly string[] = [
  'payroll_unresolved_work',
  'payroll_negative_total',
];

export interface UnresolvedWorkItem {
  class_session_id: string;
  service_date: string;
  reason: string;
}

export interface UnresolvedWorkDetails {
  count: number;
  items: UnresolvedWorkItem[];
}

/** `409 payroll_unresolved_work`'s count and items (D3, D13), or null. */
export function unresolvedWorkDetails(
  error: unknown,
): UnresolvedWorkDetails | null {
  if (!isCode(error, 'payroll_unresolved_work')) return null;
  const { count, items } = details(error);
  const list = Array.isArray(items) ? (items as unknown[]) : [];
  const parsed = list.flatMap((item): UnresolvedWorkItem[] => {
    if (typeof item !== 'object' || item === null) return [];
    const value = item as Record<string, unknown>;
    return typeof value.class_session_id === 'string' &&
      typeof value.service_date === 'string' &&
      typeof value.reason === 'string'
      ? [
          {
            class_session_id: value.class_session_id,
            service_date: value.service_date,
            reason: value.reason,
          },
        ]
      : [];
  });
  return {
    count:
      typeof count === 'number' && Number.isInteger(count) && count >= 0
        ? count
        : parsed.length,
    items: parsed,
  };
}

/** `payroll_amount_out_of_range`'s session, when a line is the cause (C8). */
export function outOfRangeSessionId(error: unknown): string | null {
  if (!isCode(error, 'payroll_amount_out_of_range')) return null;
  const id = detailString(error, 'class_session_id');
  return id && isUuid(id) ? id : null;
}

/** `403 payroll_self_finalize`'s reason (D10), or null. */
export function selfFinalizeReason(
  error: unknown,
): 'preparer' | 'own_pay' | null {
  if (!isCode(error, 'payroll_self_finalize')) return null;
  const reason = detailString(error, 'reason');
  return reason === 'preparer' || reason === 'own_pay' ? reason : null;
}

/** `409 payroll_period_out_of_order`'s oldest period (D11), or null. */
export function earliestPeriodId(error: unknown): string | null {
  if (!isCode(error, 'payroll_period_out_of_order')) return null;
  const id = detailString(error, 'earliest_period_id');
  return id && isUuid(id) ? id : null;
}

const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** `422 effective_from_under_finalized_line`'s date (G3), or null. */
export function latestFinalizedOn(error: unknown): string | null {
  if (!isCode(error, 'effective_from_under_finalized_line')) return null;
  const date = detailString(error, 'latest_finalized_on');
  return date && LOCAL_DATE.test(date) ? date : null;
}

/** The period's page. */
export function periodPath(periodId: string): string {
  return `/payroll/periods/${encodeURIComponent(periodId)}`;
}
