import { ApiError } from '@/app/api';
import type {
  CloseoutCorrectionBody,
  SessionCloseoutRecord,
} from '@/features/tutor-portal/closeout/api';
import {
  SUMMARY_FIELDS,
  summaryDraftOf,
  type SummaryDraft,
  type SummaryField,
} from '@/features/tutor-portal/closeout/rules';
import { REASON_MAX_LENGTH, reasonLength } from './rules';

// The staff closeout review's pure rules (#78 G1), mirroring the API: the
// status badge, which members a correction sends (only the changed ones;
// a cleared field is sent as null), the reason check and the errors that
// call for a reload.

export type CloseoutBadge =
  | { kind: 'submitted' }
  | { kind: 'correctionSubmitted' }
  | { kind: 'changesRequested'; reason: string | null }
  | {
      kind: 'approved';
      minutes: number;
      name: string;
      approvedAt: string;
    };

/** G1: the badge of a closeout record. */
export function closeoutBadge(record: SessionCloseoutRecord): CloseoutBadge {
  if (record.status === 'changes_requested') {
    return { kind: 'changesRequested', reason: record.review.reason };
  }
  if (record.status === 'approved' && record.approval) {
    return {
      kind: 'approved',
      minutes: record.approval.approved_minutes,
      name: record.approval.approved_by.display_name,
      approvedAt: record.approval.approved_at,
    };
  }
  return record.approval
    ? { kind: 'correctionSubmitted' }
    : { kind: 'submitted' };
}

/** The draft of the Correct dialog, prefilled from the record (shared). */
export { summaryDraftOf };

/** The reason's error code, or null (1-1000 characters once trimmed). */
export function reasonError(
  reason: string,
): 'reason_required' | 'reason_too_long' | null {
  const length = reasonLength(reason);
  if (length === 0) {
    return 'reason_required';
  }
  return length > REASON_MAX_LENGTH ? 'reason_too_long' : null;
}

/**
 * C3's body: the closeout's version, the reason (trimmed) and only the
 * members that change against the record (`actual_minutes` as a number; a
 * summary field emptied is sent as `null`). A re-approval sends no member.
 */
export function correctionBody(
  record: SessionCloseoutRecord,
  minutes: string,
  draft: SummaryDraft,
  reason: string,
  { reapprove = false }: { reapprove?: boolean } = {},
): CloseoutCorrectionBody {
  const body: CloseoutCorrectionBody = {
    version: record.version,
    reason: reason.trim(),
  };
  if (reapprove) {
    return body;
  }
  const value = Number(minutes.trim());
  if (value !== record.actual_minutes) {
    body.actual_minutes = value;
  }
  const current = summaryDraftOf(record.summary);
  const summary: Partial<Record<SummaryField, string | null>> = {};
  for (const field of SUMMARY_FIELDS) {
    const next = draft[field].trim();
    if (next !== current[field].trim()) {
      summary[field] = next === '' ? null : next;
    }
  }
  if (Object.keys(summary).length > 0) {
    body.summary = summary;
  }
  return body;
}

/** Whether a correction would change nothing (no member and no re-approval). */
export function isUnchanged(body: CloseoutCorrectionBody): boolean {
  return body.actual_minutes === undefined && body.summary === undefined;
}

const RELOAD_CODES = new Set([
  'version_conflict',
  'closeout_not_submitted',
  'closeout_with_tutor',
]);

/** G1: the decision failed because the closeout changed: offer Reload. */
export function needsReload(error: unknown): boolean {
  return error instanceof ApiError && RELOAD_CODES.has(error.code);
}
