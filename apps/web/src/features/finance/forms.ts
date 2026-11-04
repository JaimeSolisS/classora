import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import type { Vendor, VendorCreate, VendorUpdate } from './api';

// The client rules of the category and vendor dialogs (M7-11b B, C), without
// React. They only stop a request that would surely fail; the server stays
// authoritative and its refusals land on the field they are about. A message
// is a `common` key, or an `errors` key prefixed with `errors:`.

export type FinanceMessage = string;

const V = 'finance.validation';

/** The API's limits (#114). It counts code points, not UTF-16 units. */
export const CATEGORY_NAME_MAX = 100;
export const VENDOR_NAME_MAX = 200;
export const VENDOR_CONTACT_MAX = 500;

export const textLength = (value: string) => Array.from(value).length;

export function serverMessage(code: string): FinanceMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

function hasCode(error: unknown, codes: readonly string[]): error is ApiError {
  return (
    error instanceof ApiError &&
    error.code !== undefined &&
    codes.includes(error.code)
  );
}

export function isVersionConflict(error: unknown): boolean {
  return hasCode(error, ['version_conflict']);
}

export function isGone(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * The answer was lost or the server failed: the write may have been stored
 * (#458). A settled 4xx was refused and stored nothing.
 */
export function isLostAnswer(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status === undefined) {
    return true;
  }
  return error.status >= 500;
}

/** The refusals that a name is taken, which the reactivation hint explains. */
export const EXISTS_CODES: readonly string[] = [
  'expense_category_exists',
  'vendor_exists',
];

export function isNameTaken(error: unknown): boolean {
  return hasCode(error, EXISTS_CODES);
}

/** A 422's field errors on the form's fields (the first per field). */
function fieldErrors<F extends string>(
  error: unknown,
  fields: Readonly<Record<string, F>>,
): Partial<Record<F, FinanceMessage>> | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: Partial<Record<F, FinanceMessage>> = {};
  for (const item of error.fieldErrors) {
    const [where, key] = item.path;
    const field = where === 'body' ? fields[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(item.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

function validateName(
  value: string,
  max: number,
  tooLong: FinanceMessage,
): FinanceMessage | undefined {
  const name = value.trim();
  if (name === '') {
    return `${V}.nameRequired`;
  }
  return textLength(name) > max ? tooLong : undefined;
}

// ------------------------------------------------------------ categories

export interface CategoryFormValues {
  name: string;
}
export type CategoryField = keyof CategoryFormValues;
export type CategoryErrors = Partial<Record<CategoryField, FinanceMessage>>;

export function validateCategoryForm(
  values: CategoryFormValues,
): CategoryErrors {
  const name = validateName(
    values.name,
    CATEGORY_NAME_MAX,
    `${V}.categoryNameTooLong`,
  );
  return name ? { name } : {};
}

export function categoryServerErrors(error: unknown): CategoryErrors | null {
  if (hasCode(error, ['expense_category_exists'])) {
    return { name: serverMessage('expense_category_exists') };
  }
  return fieldErrors<CategoryField>(error, { name: 'name' });
}

// --------------------------------------------------------------- vendors

export interface VendorFormValues {
  name: string;
  /** As typed; '' for none. */
  contact: string;
}
export type VendorField = keyof VendorFormValues;
export type VendorErrors = Partial<Record<VendorField, FinanceMessage>>;

export function initialVendorForm(vendor: Vendor | null): VendorFormValues {
  return { name: vendor?.name ?? '', contact: vendor?.contact ?? '' };
}

export function validateVendorForm(values: VendorFormValues): VendorErrors {
  const errors: VendorErrors = {};
  const name = validateName(
    values.name,
    VENDOR_NAME_MAX,
    `${V}.vendorNameTooLong`,
  );
  if (name) {
    errors.name = name;
  }
  if (textLength(values.contact.trim()) > VENDOR_CONTACT_MAX) {
    errors.contact = `${V}.contactTooLong`;
  }
  return errors;
}

const contactOf = (values: VendorFormValues) => values.contact.trim() || null;

export function vendorCreateBody(values: VendorFormValues): VendorCreate {
  const contact = contactOf(values);
  return { name: values.name.trim(), ...(contact ? { contact } : {}) };
}

/**
 * The PATCH body of an edit: only what changed, with `contact: null` when it
 * was cleared; null when nothing changed (nothing is sent).
 */
export function vendorUpdateBody(
  values: VendorFormValues,
  base: Vendor,
): VendorUpdate | null {
  const name = values.name.trim();
  const contact = contactOf(values);
  const body: VendorUpdate = { version: base.version };
  if (name !== base.name) {
    body.name = name;
  }
  if (contact !== (base.contact ?? null)) {
    body.contact = contact;
  }
  return Object.keys(body).length > 1 ? body : null;
}

export function vendorServerErrors(error: unknown): VendorErrors | null {
  if (hasCode(error, ['vendor_exists'])) {
    return { name: serverMessage('vendor_exists') };
  }
  return fieldErrors<VendorField>(error, { name: 'name', contact: 'contact' });
}
