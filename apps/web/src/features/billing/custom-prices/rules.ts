import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import type { TargetOption } from '../targets';
import type {
  CustomPrice,
  CustomPriceCreate,
  CustomPriceListFilters,
  CustomPriceStatus,
  CustomPriceStatusChoice,
} from './api';

// The custom price page's client rules (#367 E): the list filters in the
// URL (a deep link ?student_id= or ?family_id= preselects the target), the
// add and end dialogs' checks and bodies, which rows can be ended or
// deleted, and where a server 422 goes. The server stays authoritative.

export const CUSTOM_PRICE_STATUSES: readonly CustomPriceStatus[] = [
  'current',
  'upcoming',
  'past',
];
export const STATUS_CHOICES: readonly CustomPriceStatusChoice[] = [
  'open',
  ...CUSTOM_PRICE_STATUSES,
  'all',
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function oneOf<T extends string>(
  value: string | null,
  allowed: readonly T[],
): T | null {
  return allowed.find((item) => item === value) ?? null;
}

/** The list filters from the URL (?student_id=|family_id=&status=&page=). */
export function customPriceListFilters(
  params: URLSearchParams,
): CustomPriceListFilters {
  const page = Number(params.get('page'));
  const studentId = params.get('student_id') ?? '';
  const familyId = params.get('family_id') ?? '';
  return {
    target: UUID.test(studentId)
      ? { kind: 'student', id: studentId }
      : UUID.test(familyId)
        ? { kind: 'family', id: familyId }
        : null,
    status: oneOf(params.get('status'), STATUS_CHOICES) ?? 'open',
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

/** The URL search params of the list filters (defaults left out). */
export function customPriceListParams(
  filters: CustomPriceListFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.target) {
    params.set(`${filters.target.kind}_id`, filters.target.id);
  }
  if (filters.status !== 'open') params.set('status', filters.status);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** "End" applies to a current or upcoming price not already ending today. */
export function canEnd(item: CustomPrice, today: LocalDate): boolean {
  return (
    (item.status === 'current' || item.status === 'upcoming') &&
    (item.expires_on === null || item.expires_on > today)
  );
}

/** "Delete" applies to an upcoming price no charge uses (#95 E6). */
export function canDelete(item: CustomPrice): boolean {
  return item.status === 'upcoming' && !item.in_use;
}

/** A `common` key, or an `errors` key prefixed with `errors:`. */
export type CustomPriceMessage = string;

function serverMessage(code: string): CustomPriceMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

const V = 'billing.customPrices.validation';

// ---------------------------------------------------------- the add dialog

export interface CustomPriceFormValues {
  target: TargetOption | null;
  servicePriceId: string;
  amount: string;
  effectiveFrom: string;
  /** '' for no end. */
  expiresOn: string;
}

export type CustomPriceField = keyof CustomPriceFormValues;
export type CustomPriceErrors = Partial<
  Record<CustomPriceField, CustomPriceMessage>
>;

export const CUSTOM_PRICE_FIELD_ORDER: readonly CustomPriceField[] = [
  'target',
  'servicePriceId',
  'amount',
  'effectiveFrom',
  'expiresOn',
];

export function emptyCustomPriceForm(
  today: LocalDate,
  target: TargetOption | null = null,
): CustomPriceFormValues {
  return {
    target,
    servicePriceId: '',
    amount: '',
    effectiveFrom: today,
    expiresOn: '',
  };
}

export function validateCustomPriceForm(
  values: CustomPriceFormValues,
  today: LocalDate,
): CustomPriceErrors {
  const errors: CustomPriceErrors = {};
  if (!values.target) errors.target = `${V}.targetRequired`;
  if (!values.servicePriceId) errors.servicePriceId = `${V}.serviceRequired`;
  if (normalizeMoneyInput(values.amount) === '') {
    errors.amount = `${V}.amountRequired`;
  } else if (!isMoneyInput(values.amount)) {
    errors.amount = `${V}.amountInvalid`;
  }
  if (!ISO_DATE.test(values.effectiveFrom)) {
    errors.effectiveFrom = `${V}.dateRequired`;
  } else if (values.effectiveFrom < today) {
    errors.effectiveFrom = `${V}.dateBeforeToday`;
  }
  if (values.expiresOn !== '') {
    if (!ISO_DATE.test(values.expiresOn)) {
      errors.expiresOn = `${V}.dateRequired`;
    } else if (
      !errors.effectiveFrom &&
      values.expiresOn < values.effectiveFrom
    ) {
      errors.expiresOn = `${V}.datesOrder`;
    }
  }
  return errors;
}

/** The POST body; the amount is the typed string, never a number. */
export function customPriceCreateBody(
  values: CustomPriceFormValues,
): CustomPriceCreate {
  const target = values.target!;
  return {
    service_price_id: values.servicePriceId,
    ...(target.kind === 'student'
      ? { student_id: target.id }
      : { family_id: target.id }),
    effective_from: values.effectiveFrom,
    ...(values.expiresOn ? { expires_on: values.expiresOn } : {}),
    amount: normalizeMoneyInput(values.amount),
  };
}

const BODY_FIELDS: Record<string, CustomPriceField> = {
  student_id: 'target',
  family_id: 'target',
  service_price_id: 'servicePriceId',
  amount: 'amount',
  effective_from: 'effectiveFrom',
  expires_on: 'expiresOn',
};

/**
 * A refused add's field errors on the form's fields (the first per field),
 * or null when the error belongs in the alert.
 */
export function customPriceServerErrors(
  error: unknown,
): CustomPriceErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: CustomPriceErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? BODY_FIELDS[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  // The rule's own code without a field error still belongs on the date.
  if (
    !errors.effectiveFrom &&
    error.code === 'custom_price_effective_from_invalid'
  ) {
    errors.effectiveFrom = serverMessage(error.code);
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

// ---------------------------------------------------------- the end dialog

/** The earliest end date: today, or the start when that is later. */
export function endMinDate(item: CustomPrice, today: LocalDate): LocalDate {
  return item.effective_from > today
    ? (item.effective_from as LocalDate)
    : today;
}

export function validateEndDate(
  value: string,
  min: LocalDate,
): CustomPriceMessage | undefined {
  if (!ISO_DATE.test(value)) return `${V}.dateRequired`;
  if (value < min) return `${V}.endBeforeMin`;
  return undefined;
}

/** A refused end's message for the date, or null for the alert. */
export function endServerError(error: unknown): CustomPriceMessage | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const onDate = error.fieldErrors.find(
    ({ path }) => path[0] === 'body' && path[1] === 'expires_on',
  );
  if (onDate) return serverMessage(onDate.code);
  if (error.code === 'custom_price_expiry_invalid') {
    return serverMessage(error.code);
  }
  return null;
}
