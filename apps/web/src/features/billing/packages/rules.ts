import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import {
  hoursInputToMinutes,
  MINUTES_MAX,
  MINUTES_MIN,
  SESSIONS_MAX,
  SESSIONS_MIN,
  VALIDITY_MAX,
  VALIDITY_MIN,
  type PackageUnit,
} from '@/lib/packages';
import { addDays, type LocalDate } from '@/lib/time';
import {
  PACKAGE_STATUSES,
  type PackageListFilters,
  type PackageProduct,
  type PackageProductCreate,
  type ProductListFilters,
  type SoldPackage,
} from './api';

// The package pages' client rules (#377 B-D): the list filters in the URL,
// the product dialogs' checks and bodies, where a server 422 goes, and the
// sell dialog's summary. The server stays authoritative (#94): these only
// stop a request that would surely fail.

/** A `common` key, or an `errors` key prefixed with `errors:`. */
export type PackageMessage = string;

const V = 'billing.packages.validation';

function pageParam(value: string | null): number {
  const page = Number(value);
  return Number.isInteger(page) && page > 1 ? page : 1;
}

// ------------------------------------------------------------ list filters

/** The product list filters from the URL (?is_active=&subject_id=&page=). */
export function productListFilters(
  params: URLSearchParams,
): ProductListFilters {
  const active = params.get('is_active');
  return {
    isActive: active === 'true' ? true : active === 'false' ? false : null,
    subjectId: params.get('subject_id') || null,
    page: pageParam(params.get('page')),
  };
}

export function productListParams(
  filters: ProductListFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.isActive !== null) {
    params.set('is_active', String(filters.isActive));
  }
  if (filters.subjectId) params.set('subject_id', filters.subjectId);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** The sold list filters from the URL (?status=…&student_id=&family_id=). */
export function packageListFilters(
  params: URLSearchParams,
): PackageListFilters {
  const wanted = new Set(params.getAll('status'));
  return {
    statuses: PACKAGE_STATUSES.filter((status) => wanted.has(status)),
    studentId: params.get('student_id') || null,
    familyId: params.get('family_id') || null,
    page: pageParam(params.get('page')),
  };
}

export function packageListParams(
  filters: PackageListFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  for (const status of filters.statuses) params.append('status', status);
  if (filters.studentId) params.set('student_id', filters.studentId);
  if (filters.familyId) params.set('family_id', filters.familyId);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

// ------------------------------------------------- the new-product dialog

export interface ProductFormValues {
  name: string;
  unit: PackageUnit;
  /** As typed: sessions, or hours in 0.25 steps. */
  quantity: string;
  /** As typed, in days. */
  validity: string;
  /** '' = any subject. */
  subjectId: string;
  amount: string;
  effectiveFrom: string;
}

export type ProductField = keyof ProductFormValues;

/** The form's fields in display order (the first invalid one is focused). */
export const PRODUCT_FIELD_ORDER: readonly ProductField[] = [
  'name',
  'unit',
  'quantity',
  'validity',
  'subjectId',
  'amount',
  'effectiveFrom',
];

export type ProductErrors = Partial<Record<ProductField, PackageMessage>>;

export const NAME_MAX = 120;

export function emptyProductForm(today: LocalDate): ProductFormValues {
  return {
    name: '',
    unit: 'sessions',
    quantity: '',
    validity: '',
    subjectId: '',
    amount: '',
    effectiveFrom: today,
  };
}

const WHOLE_NUMBER = /^\d+$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function nameError(name: string): PackageMessage | undefined {
  const text = name.trim();
  if (text === '') return `${V}.nameRequired`;
  if (text.length > NAME_MAX) return `${V}.nameTooLong`;
  return undefined;
}

/** The quantity in the API's unit (sessions or minutes), or null. */
export function quantityValue(values: ProductFormValues): number | null {
  const text = values.quantity.trim();
  if (values.unit === 'sessions') {
    if (!WHOLE_NUMBER.test(text)) return null;
    const sessions = Number(text);
    return sessions >= SESSIONS_MIN && sessions <= SESSIONS_MAX
      ? sessions
      : null;
  }
  const minutes = hoursInputToMinutes(text);
  return minutes !== null && minutes >= MINUTES_MIN && minutes <= MINUTES_MAX
    ? minutes
    : null;
}

export function validateProductForm(values: ProductFormValues): ProductErrors {
  const errors: ProductErrors = {};
  const name = nameError(values.name);
  if (name) errors.name = name;
  if (quantityValue(values) === null) {
    errors.quantity =
      values.unit === 'sessions' ? `${V}.sessionsRange` : `${V}.hoursRange`;
  }
  const validity = values.validity.trim();
  if (
    !WHOLE_NUMBER.test(validity) ||
    Number(validity) < VALIDITY_MIN ||
    Number(validity) > VALIDITY_MAX
  ) {
    errors.validity = `${V}.validityRange`;
  }
  if (normalizeMoneyInput(values.amount) === '') {
    errors.amount = `${V}.amountRequired`;
  } else if (!isMoneyInput(values.amount)) {
    errors.amount = `${V}.amountInvalid`;
  }
  if (!ISO_DATE.test(values.effectiveFrom)) {
    errors.effectiveFrom = `${V}.dateRequired`;
  }
  return errors;
}

/** The POST body: hours become minutes; the amount stays a string. */
export function productCreateBody(
  values: ProductFormValues,
): PackageProductCreate {
  return {
    name: values.name.trim(),
    unit: values.unit,
    quantity: quantityValue(values) ?? 0,
    validity_days: Number(values.validity.trim()),
    subject_id: values.subjectId || null,
    amount: normalizeMoneyInput(values.amount),
    effective_from: values.effectiveFrom,
  };
}

const BODY_FIELDS: Record<string, ProductField> = {
  name: 'name',
  unit: 'unit',
  quantity: 'quantity',
  validity_days: 'validity',
  subject_id: 'subjectId',
  amount: 'amount',
  effective_from: 'effectiveFrom',
};

/** The message of a server field code: its `errors` text when it has one. */
export function serverMessage(code: string): PackageMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/**
 * A 422's field errors on the product form's fields (the first per
 * field), or null when none is about a field the form has.
 */
export function productServerErrors(error: unknown): ProductErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: ProductErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? BODY_FIELDS[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

/** A 422's error on the name (the edit dialog), or null. */
export function nameServerError(error: unknown): PackageMessage | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const found = error.fieldErrors.find(
    (fieldError) =>
      fieldError.path[0] === 'body' && fieldError.path[1] === 'name',
  );
  return found ? serverMessage(found.code) : null;
}

// --------------------------------------------------------- the sale

export type SellField = 'student' | 'productId';
export type SellErrors = Partial<Record<SellField, PackageMessage>>;

/** Whether a product can be sold today (it has a price in effect). */
export function isOffered(product: Pick<PackageProduct, 'current_amount'>) {
  return product.current_amount !== null;
}

/** The expiry of a package sold today: today + validity days. */
export function expiresOn(
  product: Pick<PackageProduct, 'validity_days'>,
  today: LocalDate,
): LocalDate {
  return addDays(today, product.validity_days);
}

/** A sale's 422s on the dialog's fields, or null. */
export function sellServerErrors(error: unknown): SellErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: SellErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    if (where !== 'body') continue;
    const field =
      key === 'student_id'
        ? 'student'
        : key === 'package_product_id'
          ? 'productId'
          : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

/** Whether an error says the data changed since it was read. */
export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}

// ------------------------------------------- cancellation (#386 C1)

/** The reason's limit (PackageCancellationCreate). */
export const CANCEL_REASON_MAX = 500;

/**
 * The proposed credit in cents, as the server computes it by default
 * (#100 R6, `Money.prorate`): net × remaining / granted, rounded once half
 * away from zero. Integer arithmetic, never a float. An estimate: the
 * server decides under the account lock.
 */
export function proposedCreditCents(
  netCents: number,
  remaining: number,
  granted: number,
): number {
  if (granted <= 0 || netCents <= 0 || remaining <= 0) return 0;
  const numerator = netCents * remaining;
  return Math.floor((2 * numerator + granted) / (2 * granted));
}

/** Whether a sold package shows "Cancel" (billing.manage and active). */
export function isCancellable(pkg: Pick<SoldPackage, 'status'>): boolean {
  return pkg.status === 'active';
}

export interface CancelFormValues {
  /** Empty: the proposed credit. */
  credit: string;
  reason: string;
}

export type CancelField = keyof CancelFormValues;
export type CancelErrors = Partial<Record<CancelField, PackageMessage>>;

export const EMPTY_CANCEL_FORM: CancelFormValues = { credit: '', reason: '' };

/** The credit override from 0.00 to the purchase charge's net. */
export function validateCancelForm(
  values: CancelFormValues,
  netCents: number | null,
): CancelErrors {
  const errors: CancelErrors = {};
  const credit = normalizeMoneyInput(values.credit);
  if (credit !== '') {
    if (!isMoneyInput(credit)) {
      errors.credit = 'errors:money_invalid';
    } else if (netCents !== null && moneyCents(credit) > netCents) {
      errors.credit = 'billing.packages.cancel.creditAboveNet';
    }
  }
  const reason = values.reason.trim();
  if (reason === '') {
    errors.reason = 'billing.accounts.validation.reasonRequired';
  } else if (reason.length > CANCEL_REASON_MAX) {
    errors.reason = 'errors:too_long';
  }
  return errors;
}

/** Whole cents of a valid money input ("12.5" → 1250). */
export function moneyCents(text: string): number {
  const [whole = '0', fraction = ''] = text.trim().split('.');
  return (
    Number.parseInt(whole, 10) * 100 +
    Number.parseInt(fraction.padEnd(2, '0').slice(0, 2), 10)
  );
}

export function cancelBody(values: CancelFormValues): {
  reason: string;
  credit_amount?: string;
} {
  const credit = normalizeMoneyInput(values.credit);
  return {
    reason: values.reason.trim(),
    ...(credit !== '' ? { credit_amount: credit } : {}),
  };
}

/** A cancellation's 422s on the dialog's fields, or null. */
export function cancelServerErrors(error: unknown): CancelErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: CancelErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    if (where !== 'body') continue;
    const field =
      key === 'credit_amount' ? 'credit' : key === 'reason' ? 'reason' : null;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}
