import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type {
  CriteriaChargeKind,
  CriteriaClassType,
  DiscountAudience,
  DiscountCreate,
  DiscountDetail,
  DiscountEligibility,
  DiscountKind,
  DiscountListFilters,
  DiscountStatus,
  DiscountStatusChoice,
  DiscountUpdate,
} from './api';

// The discount pages' client rules (#367 B3, C1-C3): the list filters in
// the URL, the dialog's kind rules (#95 H2: a scholarship is for assigned
// students or families only, a sibling discount for everyone who qualifies
// and needs a minimum of active students; a promotion has both dates), the
// value checks, the create body, the edit body with only the changed
// fields, and where a server 422 goes. The server stays authoritative.

/** The kinds in the dialog's order. */
export const DISCOUNT_KINDS: readonly DiscountKind[] = [
  'discount',
  'scholarship',
  'sibling',
  'promotion',
];
export const DISCOUNT_STATUSES: readonly DiscountStatus[] = [
  'current',
  'upcoming',
  'expired',
  'inactive',
];
export const STATUS_CHOICES: readonly DiscountStatusChoice[] = [
  'open',
  ...DISCOUNT_STATUSES,
  'all',
];
export const CHARGE_KINDS: readonly CriteriaChargeKind[] = [
  'session',
  'group_enrollment',
  'cohort_enrollment',
  'tuition_period',
  'package_purchase',
];
export const CRITERIA_CLASS_TYPES: readonly CriteriaClassType[] = [
  'individual',
  'group',
  'course',
];

export const NAME_MAX_LENGTH = 120;
export const MIN_FAMILY_MIN = 2;
export const MIN_FAMILY_MAX = 20;
export const Q_MAX_LENGTH = 120;

/** The audience a kind forces (#95 H2), if any. */
export function forcedAudience(kind: DiscountKind): DiscountAudience | null {
  if (kind === 'scholarship') return 'assigned';
  if (kind === 'sibling') return 'all';
  return null;
}

// ------------------------------------------------------------ list filters

function oneOf<T extends string>(
  value: string | null,
  allowed: readonly T[],
): T | null {
  return allowed.find((item) => item === value) ?? null;
}

/** The list filters from the URL (?kind=&status=&q=&page=). */
export function discountListFilters(
  params: URLSearchParams,
): DiscountListFilters {
  const page = Number(params.get('page'));
  return {
    kind: oneOf(params.get('kind'), DISCOUNT_KINDS),
    status: oneOf(params.get('status'), STATUS_CHOICES) ?? 'open',
    q: (params.get('q') ?? '').trim().slice(0, Q_MAX_LENGTH),
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

/** The URL search params of the list filters (defaults left out). */
export function discountListParams(
  filters: DiscountListFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.status !== 'open') params.set('status', filters.status);
  if (filters.q) params.set('q', filters.q);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

// ------------------------------------------------------------- the dialog

export type ValueType = 'percent' | 'amount';

export interface DiscountFormValues {
  kind: DiscountKind;
  name: string;
  valueType: ValueType;
  /** The percentage or the amount, as typed. */
  value: string;
  audience: DiscountAudience;
  chargeKinds: CriteriaChargeKind[];
  classTypes: CriteriaClassType[];
  subjectIds: string[];
  levelIds: string[];
  /** As typed; sibling discounts only. */
  minFamilyStudents: string;
  startsOn: string;
  expiresOn: string;
  isActive: boolean;
}

export type DiscountField = keyof DiscountFormValues;

/** The fields in display order (the first invalid one is focused). */
export const DISCOUNT_FIELD_ORDER: readonly DiscountField[] = [
  'kind',
  'name',
  'value',
  'audience',
  'chargeKinds',
  'classTypes',
  'subjectIds',
  'levelIds',
  'minFamilyStudents',
  'startsOn',
  'expiresOn',
  'isActive',
];

/** A `common` key, or an `errors` key prefixed with `errors:`. */
export type DiscountMessage = string;
export type DiscountErrors = Partial<Record<DiscountField, DiscountMessage>>;

const V = 'billing.discounts.validation';

export function emptyDiscountForm(): DiscountFormValues {
  return {
    kind: 'discount',
    name: '',
    valueType: 'percent',
    value: '',
    audience: 'all',
    chargeKinds: [],
    classTypes: [],
    subjectIds: [],
    levelIds: [],
    minFamilyStudents: '',
    startsOn: '',
    expiresOn: '',
    isActive: true,
  };
}

/** "10.0000" → "10", "12.5000" → "12.5" (the API sends four places). */
export function trimPercent(percent: string): string {
  return percent.includes('.')
    ? percent.replace(/0+$/, '').replace(/\.$/, '')
    : percent;
}

/** The form of an existing discount (the edit dialog). */
export function discountFormOf(discount: DiscountDetail): DiscountFormValues {
  const { eligibility } = discount;
  return {
    kind: discount.kind,
    name: discount.name,
    valueType: discount.percent !== null ? 'percent' : 'amount',
    value:
      discount.percent !== null
        ? trimPercent(discount.percent)
        : (discount.amount ?? ''),
    audience: discount.audience,
    chargeKinds: [...(eligibility.charge_kinds ?? [])],
    classTypes: [...(eligibility.class_types ?? [])],
    subjectIds: (eligibility.subjects ?? []).map((subject) => subject.id),
    levelIds: (eligibility.education_levels ?? []).map((level) => level.id),
    minFamilyStudents:
      eligibility.min_family_students === null
        ? ''
        : String(eligibility.min_family_students),
    startsOn: discount.starts_on ?? '',
    expiresOn: discount.expires_on ?? '',
    isActive: discount.is_active,
  };
}

/**
 * The values after one field changed, with the kind rules applied: a
 * scholarship is "assigned", a sibling discount "everyone", and only a
 * sibling discount keeps a minimum of active students.
 */
export function changeDiscountForm<K extends DiscountField>(
  values: DiscountFormValues,
  field: K,
  value: DiscountFormValues[K],
): DiscountFormValues {
  const next = { ...values, [field]: value };
  const forced = forcedAudience(next.kind);
  if (forced) {
    next.audience = forced;
  }
  if (next.kind !== 'sibling') {
    next.minFamilyStudents = '';
  }
  return next;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const WHOLE_NUMBER = /^\d+$/;
const PERCENT = /^\d{1,3}(\.\d+)?$/;

function percentError(value: string): DiscountMessage | undefined {
  if (value === '') return `${V}.valueRequired`;
  if (!PERCENT.test(value)) return `${V}.percentInvalid`;
  const decimals = value.split('.')[1] ?? '';
  if (decimals.length > 4) return `${V}.percentPrecision`;
  const number = Number(value);
  if (number <= 0 || number > 100) return `${V}.percentRange`;
  return undefined;
}

function amountError(value: string): DiscountMessage | undefined {
  if (value === '') return `${V}.valueRequired`;
  if (!isMoneyInput(value)) return `${V}.amountInvalid`;
  if (Number(value) === 0) return `${V}.amountPositive`;
  return undefined;
}

export function validateDiscountForm(
  values: DiscountFormValues,
): DiscountErrors {
  const errors: DiscountErrors = {};
  const name = values.name.trim();
  if (name === '') errors.name = `${V}.nameRequired`;
  else if (name.length > NAME_MAX_LENGTH) errors.name = `${V}.nameTooLong`;
  const value = values.value.trim();
  const valueError =
    values.valueType === 'percent' ? percentError(value) : amountError(value);
  if (valueError) errors.value = valueError;
  if (values.kind === 'sibling') {
    const minimum = values.minFamilyStudents.trim();
    if (minimum === '') {
      errors.minFamilyStudents = `${V}.minFamilyRequired`;
    } else if (
      !WHOLE_NUMBER.test(minimum) ||
      Number(minimum) < MIN_FAMILY_MIN ||
      Number(minimum) > MIN_FAMILY_MAX
    ) {
      errors.minFamilyStudents = `${V}.minFamilyRange`;
    }
  }
  for (const field of ['startsOn', 'expiresOn'] as const) {
    const date = values[field];
    if (date === '') {
      if (values.kind === 'promotion') errors[field] = `${V}.datesRequired`;
    } else if (!ISO_DATE.test(date)) {
      errors[field] = `${V}.dateInvalid`;
    }
  }
  if (
    !errors.startsOn &&
    !errors.expiresOn &&
    values.startsOn !== '' &&
    values.expiresOn !== '' &&
    values.startsOn > values.expiresOn
  ) {
    errors.expiresOn = `${V}.datesOrder`;
  }
  return errors;
}

/** The criteria of the form (every empty list left out = any). */
export function eligibilityOf(values: DiscountFormValues): DiscountEligibility {
  const sorted = (list: readonly string[]) => [...list].sort();
  return {
    ...(values.chargeKinds.length > 0
      ? {
          charge_kinds: CHARGE_KINDS.filter((kind) =>
            values.chargeKinds.includes(kind),
          ),
        }
      : {}),
    ...(values.classTypes.length > 0
      ? {
          class_types: CRITERIA_CLASS_TYPES.filter((type) =>
            values.classTypes.includes(type),
          ),
        }
      : {}),
    ...(values.subjectIds.length > 0
      ? { subject_ids: sorted(values.subjectIds) }
      : {}),
    ...(values.levelIds.length > 0
      ? { education_level_ids: sorted(values.levelIds) }
      : {}),
    ...(values.kind === 'sibling' && values.minFamilyStudents.trim() !== ''
      ? { min_family_students: Number(values.minFamilyStudents.trim()) }
      : {}),
  };
}

/** The POST body; the percentage and the amount stay strings. */
export function discountCreateBody(values: DiscountFormValues): DiscountCreate {
  const value =
    values.valueType === 'percent'
      ? values.value.trim()
      : normalizeMoneyInput(values.value);
  return {
    kind: values.kind,
    audience: forcedAudience(values.kind) ?? values.audience,
    name: values.name.trim(),
    ...(values.valueType === 'percent'
      ? { percent: value }
      : { amount: value }),
    ...(values.startsOn ? { starts_on: values.startsOn } : {}),
    ...(values.expiresOn ? { expires_on: values.expiresOn } : {}),
    eligibility: eligibilityOf(values),
    is_active: true,
  };
}

/**
 * The PATCH body: `version` and only what changed from `initial`.
 * Switching between a percentage and an amount sends the other as null;
 * a criteria change sends the whole object; a cleared date is null.
 */
export function discountUpdateBody(
  initial: DiscountFormValues,
  values: DiscountFormValues,
  version: number,
): DiscountUpdate {
  const body: DiscountUpdate = { version };
  const name = values.name.trim();
  if (name !== initial.name.trim()) body.name = name;
  const value = values.value.trim();
  if (values.valueType !== initial.valueType || value !== initial.value) {
    if (values.valueType === 'percent') {
      body.percent = value;
      if (initial.valueType === 'amount') body.amount = null;
    } else {
      body.amount = value;
      if (initial.valueType === 'percent') body.percent = null;
    }
  }
  const eligibility = eligibilityOf(values);
  if (JSON.stringify(eligibility) !== JSON.stringify(eligibilityOf(initial))) {
    body.eligibility = eligibility;
  }
  if (values.startsOn !== initial.startsOn) {
    body.starts_on = values.startsOn || null;
  }
  if (values.expiresOn !== initial.expiresOn) {
    body.expires_on = values.expiresOn || null;
  }
  if (values.isActive !== initial.isActive) body.is_active = values.isActive;
  return body;
}

const BODY_FIELDS: Record<string, DiscountField> = {
  kind: 'kind',
  audience: 'audience',
  name: 'name',
  percent: 'value',
  amount: 'value',
  starts_on: 'startsOn',
  expires_on: 'expiresOn',
  is_active: 'isActive',
};

const ELIGIBILITY_FIELDS: Record<string, DiscountField> = {
  charge_kinds: 'chargeKinds',
  class_types: 'classTypes',
  subject_ids: 'subjectIds',
  education_level_ids: 'levelIds',
  min_family_students: 'minFamilyStudents',
};

/** The message of a server field code: its `errors` text when it has one. */
function serverMessage(code: string): DiscountMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

function fieldOfPath(path: readonly (string | number)[]): DiscountField | null {
  const [where, key, inner] = path;
  if (where !== 'body') return null;
  if (key === 'eligibility') {
    // min_family_students_required/_not_allowed sit on the object (#95 D2).
    return inner === undefined
      ? 'minFamilyStudents'
      : (ELIGIBILITY_FIELDS[String(inner)] ?? null);
  }
  return BODY_FIELDS[String(key)] ?? null;
}

/**
 * Where a refused save goes: the 422 field errors on their fields (the
 * first per field), a 409 discount_name_exists on the name; null when the
 * error belongs in the alert.
 */
export function discountServerErrors(error: unknown): DiscountErrors | null {
  if (!(error instanceof ApiError)) return null;
  if (error.code === 'discount_name_exists') {
    return { name: serverMessage(error.code) };
  }
  if (error.status !== 422) return null;
  const errors: DiscountErrors = {};
  for (const fieldError of error.fieldErrors) {
    const field = fieldOfPath(fieldError.path);
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}
