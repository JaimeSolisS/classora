import { ApiError } from '@/app/api';
import type { SessionOverride } from '../create/rules';
import type { ChangeRequestApprove, ChangeRequestOutcome } from './api';

// The rules of the change request review page (#62 G1), without React.

/** The most characters of a decision reason once trimmed (#40). */
export const DECISION_REASON_MAX_LENGTH = 1000;

/** The room choice of a reschedule: keep the session's room, or no room. */
export const KEEP_ROOM = '';
export const NO_ROOM = 'none';

export interface ApproveChoice {
  /** Approve as an exception (excused); needs a reason. */
  exception: boolean;
  reason?: string;
  /** A reschedule's room: KEEP_ROOM, NO_ROOM or a classroom id. */
  room?: string;
  override?: SessionOverride;
}

/**
 * The approve body: the request's version, the exception and its reason,
 * the room of a reschedule (absent keeps it, null is no room) and the
 * override when one is given (#49). A cancellation sends no room.
 */
export function approveBody(
  version: number,
  kind: 'cancellation' | 'reschedule',
  choice: ApproveChoice,
): ChangeRequestApprove {
  const reason = choice.reason?.trim();
  const body: ChangeRequestApprove = { version, exception: choice.exception };
  if (reason) {
    body.reason = reason;
  }
  if (kind === 'reschedule') {
    if (choice.room === NO_ROOM) {
      body.classroom_id = null;
    } else if (choice.room) {
      body.classroom_id = choice.room;
    }
    if (choice.override) {
      body.override = choice.override;
    }
  }
  return body;
}

/** The reason dialog's check: 1-1000 characters once trimmed. */
export function decisionReasonProblem(
  reason: string,
): 'required' | 'tooLong' | null {
  const length = reason.trim().length;
  if (length === 0) {
    return 'required';
  }
  return length > DECISION_REASON_MAX_LENGTH ? 'tooLong' : null;
}

const STALE_CODES: ReadonlySet<string> = new Set([
  'change_request_not_pending',
  'change_request_session_started',
  'change_request_session_changed',
  'version_conflict',
]);

/**
 * A decision refused because the request or its session changed (`409
 * change_request_not_pending` / `_session_started` / `_session_changed` /
 * `version_conflict`): the page shows a message and reloads the request.
 */
export function isStaleChangeRequestError(error: unknown): boolean {
  return error instanceof ApiError && STALE_CODES.has(error.code);
}

export type StaleMessageKey =
  | 'scheduling.changeRequest.stale.notPending'
  | 'scheduling.changeRequest.stale.started'
  | 'scheduling.changeRequest.stale.sessionChanged'
  | 'scheduling.changeRequest.stale.changed';

/** The page-level message key of a stale decision. */
export function staleMessageKey(error: unknown): StaleMessageKey {
  if (error instanceof ApiError) {
    if (error.code === 'change_request_not_pending') {
      return 'scheduling.changeRequest.stale.notPending';
    }
    if (error.code === 'change_request_session_started') {
      return 'scheduling.changeRequest.stale.started';
    }
    if (error.code === 'change_request_session_changed') {
      return 'scheduling.changeRequest.stale.sessionChanged';
    }
  }
  return 'scheduling.changeRequest.stale.changed';
}

export type ClassificationKey =
  | 'scheduling.changeRequest.classification.on_time'
  | 'scheduling.changeRequest.classification.late'
  | 'scheduling.changeRequest.classification.no_show'
  | 'scheduling.changeRequest.classification.excused';

/** The label key of a policy classification. */
export function classificationKey(
  outcome: Pick<ChangeRequestOutcome, 'classification'>,
): ClassificationKey {
  return `scheduling.changeRequest.classification.${outcome.classification}`;
}

/** The system codes of a cancellation by request (#62 C2 step 9). */
export const REQUEST_CANCELLATION_REASONS: ReadonlySet<string> = new Set([
  'family_request',
  'tutor_request',
]);

/** The page of a change request (#62 G1). */
export function changeRequestPath(requestId: string): string {
  return `/schedule/change-requests/${encodeURIComponent(requestId)}`;
}
