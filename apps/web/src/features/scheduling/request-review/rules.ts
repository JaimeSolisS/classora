import { ApiError } from '@/app/api';
import type { TutorSuggestionParams } from '../create/api';
import type { SessionOverride } from '../create/rules';
import { respondByInstant, type OfferValues } from '../session/rules';
import type { SessionRequestConfirm, SessionRequestReview } from './api';

// The rules of the review page (#60 H1), without React.

/** The most characters of a rejection reason once trimmed (#40). */
export const REJECTION_REASON_MAX_LENGTH = 1000;

/**
 * The confirm body: the request's version, the chosen tutor, the optional
 * classroom and earlier deadline (a local date and time in the center zone
 * → an instant), and the override when one is given (#49).
 */
export function confirmBody(
  values: OfferValues,
  version: number,
  timeZone: string,
  override?: SessionOverride,
): SessionRequestConfirm {
  const respondBy = respondByInstant(values, timeZone);
  return {
    version,
    tutor_user_id: values.tutorId,
    ...(values.classroomId ? { classroom_id: values.classroomId } : {}),
    ...(respondBy ? { respond_by: respondBy } : {}),
    ...(override ? { override } : {}),
  };
}

/** The #51 suggestions query for the request's slot (and chosen room). */
export function requestSuggestionParams(
  request: Pick<SessionRequestReview, 'subject' | 'starts_at' | 'ends_at'>,
  classroomId?: string,
): TutorSuggestionParams {
  return {
    subject_id: request.subject.id,
    starts_at: request.starts_at,
    ends_at: request.ends_at,
    ...(classroomId ? { classroom_id: classroomId } : {}),
  };
}

/** The reject dialog's check: 1-1000 characters once trimmed. */
export function rejectionReasonProblem(
  reason: string,
): 'required' | 'tooLong' | null {
  const length = reason.trim().length;
  if (length === 0) {
    return 'required';
  }
  return length > REJECTION_REASON_MAX_LENGTH ? 'tooLong' : null;
}

/**
 * A decision refused because the request changed or its time began (`409
 * session_request_not_pending` / `session_request_started` /
 * `version_conflict`): the page shows a message and reloads the request.
 */
export function isStaleRequestError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.code === 'session_request_not_pending' ||
      error.code === 'session_request_started' ||
      error.code === 'version_conflict')
  );
}

/** The page-level message key of a stale decision. */
export function staleMessageKey(
  error: unknown,
):
  | 'scheduling.requestReview.stale.notPending'
  | 'scheduling.requestReview.stale.started'
  | 'scheduling.requestReview.stale.changed' {
  if (error instanceof ApiError && error.code === 'session_request_started') {
    return 'scheduling.requestReview.stale.started';
  }
  if (
    error instanceof ApiError &&
    error.code === 'session_request_not_pending'
  ) {
    return 'scheduling.requestReview.stale.notPending';
  }
  return 'scheduling.requestReview.stale.changed';
}
