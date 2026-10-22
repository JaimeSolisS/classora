import type { FieldError } from '@classora/api-client';
import { ApiError } from '@/app/api';
import { localDateTimeToInstant } from '@/lib/time';
import type { SessionOverride } from '../create/rules';
import type {
  AssignmentOfferCreate,
  ClassSeriesOfferCreate,
  ClassSessionDetail,
  SubstitutionCreate,
} from './api';

// The rules of the "Offer to a tutor" form (#52 H2), without React: the
// values, the client checks, the body (an optional earlier deadline as a
// local date and time in the center zone → an instant) and where the
// server's 422 field errors go.

export interface OfferValues {
  tutorId: string;
  /** YYYY-MM-DD in the center zone; '' = the default deadline. */
  deadlineDate: string;
  /** HH:MM in the center zone; '' = the default deadline. */
  deadlineTime: string;
  /** Why the tutor is replaced (#55 H1; the substitution form only). */
  reason?: string;
}

export const EMPTY_OFFER: OfferValues = {
  tutorId: '',
  deadlineDate: '',
  deadlineTime: '',
  reason: '',
};

export type OfferField = 'tutor' | 'deadline' | 'reason';

/** The most characters of a substitution or cancellation reason (#55). */
export const REASON_MAX_LENGTH = 1000;

/** A reason's length once trimmed (the counter). */
export function reasonLength(reason: string): number {
  return reason.trim().length;
}

export type OfferMessage =
  | 'scheduling.offer.validation.tutorRequired'
  | 'scheduling.offer.validation.deadlineIncomplete'
  | 'scheduling.substitution.validation.reasonRequired'
  | 'scheduling.substitution.validation.reasonTooLong'
  | 'errors:tutor_unknown'
  | 'errors:tutor_inactive'
  | 'errors:substitute_is_current_tutor'
  | 'errors:respond_by_invalid'
  | 'errors:validation_error';

export type OfferErrors = Partial<Record<OfferField, OfferMessage>>;

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The client checks: a tutor, and both deadline parts or neither; with
 * `requireReason` (the substitution form), a reason of 1-1000 characters.
 */
export function validateOffer(
  values: OfferValues,
  { requireReason = false }: { requireReason?: boolean } = {},
): OfferErrors {
  const errors: OfferErrors = {};
  if (requireReason) {
    const length = reasonLength(values.reason ?? '');
    if (length === 0) {
      errors.reason = 'scheduling.substitution.validation.reasonRequired';
    } else if (length > REASON_MAX_LENGTH) {
      errors.reason = 'scheduling.substitution.validation.reasonTooLong';
    }
  }
  if (!values.tutorId) {
    errors.tutor = 'scheduling.offer.validation.tutorRequired';
  }
  const date = values.deadlineDate.trim();
  const time = values.deadlineTime.trim();
  if ((date || time) && !(DATE.test(date) && TIME.test(time))) {
    errors.deadline = 'scheduling.offer.validation.deadlineIncomplete';
  }
  return errors;
}

/**
 * The deadline as an instant: the local date and time in `timeZone`, or
 * null for the default deadline (no date and no time).
 */
export function respondByInstant(
  values: Pick<OfferValues, 'deadlineDate' | 'deadlineTime'>,
  timeZone: string,
): string | null {
  if (!values.deadlineDate && !values.deadlineTime) {
    return null;
  }
  return localDateTimeToInstant(
    values.deadlineDate,
    values.deadlineTime,
    timeZone,
  );
}

/** The POST body, with the override when one is given (#49). */
export function offerBody(
  values: OfferValues,
  version: number,
  timeZone: string,
  override?: SessionOverride,
): AssignmentOfferCreate {
  const respondBy = respondByInstant(values, timeZone);
  return {
    tutor_user_id: values.tutorId,
    version,
    ...(respondBy ? { respond_by: respondBy } : {}),
    ...(override ? { override } : {}),
  };
}

/** The substitution's POST body (#55 B2), with the override when given. */
export function substitutionBody(
  values: OfferValues,
  version: number,
  timeZone: string,
  override?: SessionOverride,
): SubstitutionCreate {
  const respondBy = respondByInstant(values, timeZone);
  return {
    substitute_tutor_user_id: values.tutorId,
    version,
    reason: (values.reason ?? '').trim(),
    ...(respondBy ? { respond_by: respondBy } : {}),
    ...(override ? { override } : {}),
  };
}

/** The server's 422 field errors on the form's fields (others left out). */
export function offerServerErrors(
  fieldErrors: readonly FieldError[],
): OfferErrors {
  const errors: OfferErrors = {};
  for (const { path, code } of fieldErrors) {
    const name = String(path[path.length - 1]);
    if (
      (name === 'tutor_user_id' || name === 'substitute_tutor_user_id') &&
      !errors.tutor
    ) {
      errors.tutor =
        code === 'tutor_unknown' ||
        code === 'tutor_inactive' ||
        code === 'substitute_is_current_tutor'
          ? `errors:${code}`
          : 'errors:validation_error';
    } else if (name === 'reason' && path.length === 2 && !errors.reason) {
      errors.reason =
        code === 'reason_too_long'
          ? 'scheduling.substitution.validation.reasonTooLong'
          : 'scheduling.substitution.validation.reasonRequired';
    } else if (name === 'respond_by' && !errors.deadline) {
      errors.deadline =
        code === 'respond_by_invalid'
          ? 'errors:respond_by_invalid'
          : 'errors:validation_error';
    }
  }
  return errors;
}

/** Whom the panel offers to (#233 G2): this session, or the series from it. */
export type OfferScope = 'session' | 'series';

/**
 * The body of "This and following sessions" (#233 C2): the series version
 * and this occurrence's local date; never an override (E3).
 */
export function seriesOfferBody(
  values: OfferValues,
  seriesVersion: number,
  fromDate: string,
  timeZone: string,
): ClassSeriesOfferCreate {
  const respondBy = respondByInstant(values, timeZone);
  return {
    tutor_user_id: values.tutorId,
    version: seriesVersion,
    from_date: fromDate,
    ...(respondBy ? { respond_by: respondBy } : {}),
  };
}

/** A series 409's conflicts grouped by local date, in date order. */
export function conflictsByDate<T extends { details: Record<string, unknown> }>(
  conflicts: readonly T[],
): { date: string; conflicts: T[] }[] {
  const groups = new Map<string, T[]>();
  for (const conflict of conflicts) {
    const date =
      typeof conflict.details.date === 'string' ? conflict.details.date : '';
    groups.set(date, [...(groups.get(date) ?? []), conflict]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, list]) => ({ date, conflicts: list }));
}

// --- Substitutions and cancellations (#55 H1) ---------------------------------

const CANCELLABLE: readonly ClassSessionDetail['state'][] = [
  'unassigned',
  'offered',
  'confirmed',
  'conflict',
];

/**
 * Whether the "Find a substitute" panel shows: a confirmed session that
 * hasn't started, without a pending substitution, for a holder of
 * schedule.assign.
 */
export function canSubstitute(
  session: Pick<
    ClassSessionDetail,
    'state' | 'starts_at' | 'pending_substitution'
  >,
  now: Date,
  canAssign: boolean,
): boolean {
  return (
    canAssign &&
    session.state === 'confirmed' &&
    Date.parse(session.starts_at) > now.getTime() &&
    session.pending_substitution === null
  );
}

/**
 * Whether "Cancel session" shows: an unassigned, offered, confirmed or
 * conflict session that hasn't started, for a holder of schedule.manage.
 */
export function canCancel(
  session: Pick<ClassSessionDetail, 'state' | 'starts_at'>,
  now: Date,
  canManage: boolean,
): boolean {
  return (
    canManage &&
    CANCELLABLE.includes(session.state) &&
    Date.parse(session.starts_at) > now.getTime()
  );
}

/**
 * A write refused because the session changed (`409
 * class_session_not_substitutable` / `_not_cancellable` /
 * `version_conflict` / `offer_not_pending`): the page shows the message and
 * reloads the session.
 */
export function isStaleSessionError(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.code === 'class_session_not_substitutable' ||
      error.code === 'class_session_not_cancellable' ||
      error.code === 'version_conflict' ||
      error.code === 'offer_not_pending')
  );
}
