import type { FieldError } from '@classora/api-client';
import { localDateTimeToInstant } from '@/lib/time';
import type { SessionOverride } from '../create/rules';
import type { AssignmentOfferCreate } from './api';

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
}

export const EMPTY_OFFER: OfferValues = {
  tutorId: '',
  deadlineDate: '',
  deadlineTime: '',
};

export type OfferField = 'tutor' | 'deadline';

export type OfferMessage =
  | 'scheduling.offer.validation.tutorRequired'
  | 'scheduling.offer.validation.deadlineIncomplete'
  | 'errors:tutor_unknown'
  | 'errors:tutor_inactive'
  | 'errors:respond_by_invalid'
  | 'errors:validation_error';

export type OfferErrors = Partial<Record<OfferField, OfferMessage>>;

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** The client checks: a tutor, and both deadline parts or neither. */
export function validateOffer(values: OfferValues): OfferErrors {
  const errors: OfferErrors = {};
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

/** The server's 422 field errors on the form's fields (others left out). */
export function offerServerErrors(
  fieldErrors: readonly FieldError[],
): OfferErrors {
  const errors: OfferErrors = {};
  for (const { path, code } of fieldErrors) {
    const name = String(path[path.length - 1]);
    if (name === 'tutor_user_id' && !errors.tutor) {
      errors.tutor =
        code === 'tutor_unknown' || code === 'tutor_inactive'
          ? `errors:${code}`
          : 'errors:validation_error';
    } else if (name === 'respond_by' && !errors.deadline) {
      errors.deadline =
        code === 'respond_by_invalid'
          ? 'errors:respond_by_invalid'
          : 'errors:validation_error';
    }
  }
  return errors;
}
