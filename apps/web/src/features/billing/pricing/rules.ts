import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import { addDays, type LocalDate } from '@/lib/time';
import type {
  ClassType,
  PriceListFilters,
  PricingModel,
  PriceVersion,
  PriceVersionCreate,
  ScopeKind,
  ServicePriceCreate,
} from './api';

// The price catalog's client rules (#363 B3, C1-C3, D3): the pricing models
// each class type takes, the list filters in the URL, the two dialogs'
// checks and bodies, and where a server 422 goes. The server stays
// authoritative (#89): these only stop a request that would surely fail.

export const CLASS_TYPES: readonly ClassType[] = [
  'individual',
  'group',
  'course',
];
export const PRICING_MODELS: readonly PricingModel[] = [
  'per_session',
  'full_course',
];

/** The pricing models a class type takes (#89 B3). */
export const MODELS_BY_CLASS: Record<ClassType, readonly PricingModel[]> = {
  individual: ['per_session'],
  group: ['per_session', 'full_course'],
  course: ['full_course'],
};

/** What a price of the class type can be scoped to, if anything. */
export function scopeKindOf(classType: ClassType | ''): ScopeKind | null {
  if (classType === 'course') return 'cohort';
  if (classType === 'group') return 'group';
  return null;
}

export const DURATION_MIN = 15;
export const DURATION_MAX = 480;

// ------------------------------------------------------------ list filters

function oneOf<T extends string>(
  value: string | null,
  allowed: readonly T[],
): T | null {
  return allowed.find((item) => item === value) ?? null;
}

/** The list filters from the URL (?class_type=&pricing_model=&page=). */
export function priceListFilters(params: URLSearchParams): PriceListFilters {
  const page = Number(params.get('page'));
  return {
    classType: oneOf(params.get('class_type'), CLASS_TYPES),
    pricingModel: oneOf(params.get('pricing_model'), PRICING_MODELS),
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

/** The URL search params of the list filters (defaults left out). */
export function priceListParams(filters: PriceListFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.classType) params.set('class_type', filters.classType);
  if (filters.pricingModel) params.set('pricing_model', filters.pricingModel);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

// ------------------------------------------------------- the add dialog

export interface PriceFormValues {
  classType: ClassType | '';
  pricingModel: PricingModel | '';
  subjectId: string;
  /** A cohort (course) or group (group) id; '' for none. */
  scopeId: string;
  /** '' for any level. */
  levelId: string;
  /** As typed; '' for any duration. */
  duration: string;
  effectiveFrom: string;
  amount: string;
}

export type PriceField = keyof PriceFormValues;

/** The form's fields in display order (the first invalid one is focused). */
export const PRICE_FIELD_ORDER: readonly PriceField[] = [
  'classType',
  'pricingModel',
  'subjectId',
  'scopeId',
  'levelId',
  'duration',
  'effectiveFrom',
  'amount',
];

/** A `common` key, or an `errors` key prefixed with `errors:`. */
export type PriceMessage = string;
export type PriceErrors = Partial<Record<PriceField, PriceMessage>>;

const V = 'billing.prices.validation';

export function emptyPriceForm(today: LocalDate): PriceFormValues {
  return {
    classType: '',
    pricingModel: '',
    subjectId: '',
    scopeId: '',
    levelId: '',
    duration: '',
    effectiveFrom: today,
    amount: '',
  };
}

/**
 * The values after one field changed, with the dependent fields kept
 * consistent: the model follows the class type (pre-selected when only one
 * applies), a scope clears the level and the duration, the scope clears
 * when the subject or the class type changes, and only per-session prices
 * have a duration.
 */
export function changePriceForm<K extends PriceField>(
  values: PriceFormValues,
  field: K,
  value: PriceFormValues[K],
): PriceFormValues {
  const next = { ...values, [field]: value };
  if (field === 'classType') {
    const models = next.classType ? MODELS_BY_CLASS[next.classType] : [];
    if (models.length === 1) {
      next.pricingModel = models[0]!;
    } else if (next.pricingModel && !models.includes(next.pricingModel)) {
      next.pricingModel = '';
    }
    next.scopeId = '';
  }
  if (field === 'subjectId') {
    next.scopeId = '';
  }
  if (next.scopeId) {
    next.levelId = '';
    next.duration = '';
  }
  if (next.pricingModel !== 'per_session') {
    next.duration = '';
  }
  return next;
}

const WHOLE_NUMBER = /^\d+$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function amountError(amount: string): PriceMessage | undefined {
  if (normalizeMoneyInput(amount) === '') return `${V}.amountRequired`;
  if (!isMoneyInput(amount)) return `${V}.amountInvalid`;
  return undefined;
}

function dateError(value: string, min?: LocalDate): PriceMessage | undefined {
  if (!ISO_DATE.test(value)) return `${V}.dateRequired`;
  if (min && value < min) return `${V}.dateBeforeMin`;
  return undefined;
}

export function validatePriceForm(values: PriceFormValues): PriceErrors {
  const errors: PriceErrors = {};
  if (!values.classType) errors.classType = `${V}.classTypeRequired`;
  if (
    !values.pricingModel ||
    (values.classType &&
      !MODELS_BY_CLASS[values.classType].includes(values.pricingModel))
  ) {
    errors.pricingModel = `${V}.pricingModelRequired`;
  }
  if (!values.subjectId) errors.subjectId = `${V}.subjectRequired`;
  const duration = values.duration.trim();
  if (duration !== '') {
    const minutes = Number(duration);
    if (
      !WHOLE_NUMBER.test(duration) ||
      minutes < DURATION_MIN ||
      minutes > DURATION_MAX
    ) {
      errors.duration = `${V}.durationRange`;
    }
  }
  const date = dateError(values.effectiveFrom);
  if (date) errors.effectiveFrom = date;
  const amount = amountError(values.amount);
  if (amount) errors.amount = amount;
  return errors;
}

/** The POST body; the amount is the typed string, never a number. */
export function priceCreateBody(values: PriceFormValues): ServicePriceCreate {
  const scope = scopeKindOf(values.classType);
  const duration = values.duration.trim();
  return {
    class_type: values.classType as ClassType,
    pricing_model: values.pricingModel as PricingModel,
    subject_id: values.subjectId,
    ...(values.scopeId && scope === 'cohort'
      ? { cohort_id: values.scopeId }
      : {}),
    ...(values.scopeId && scope === 'group'
      ? { group_id: values.scopeId }
      : {}),
    ...(values.levelId && !values.scopeId
      ? { education_level_id: values.levelId }
      : {}),
    ...(duration && !values.scopeId && values.pricingModel === 'per_session'
      ? { duration_minutes: Number(duration) }
      : {}),
    effective_from: values.effectiveFrom,
    amount: normalizeMoneyInput(values.amount),
  };
}

const BODY_FIELDS: Record<string, PriceField> = {
  class_type: 'classType',
  pricing_model: 'pricingModel',
  subject_id: 'subjectId',
  cohort_id: 'scopeId',
  group_id: 'scopeId',
  education_level_id: 'levelId',
  duration_minutes: 'duration',
  effective_from: 'effectiveFrom',
  amount: 'amount',
};

/** The message of a server field code: its `errors` text when it has one. */
function serverMessage(code: string): PriceMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/**
 * A 422's field errors on the form's fields (the first per field), or null
 * when none is about a field the form has.
 */
export function priceServerErrors(error: unknown): PriceErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: PriceErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? BODY_FIELDS[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

/** The existing item's id of a 409 service_price_exists, if any. */
export function existingPriceId(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.code !== 'service_price_exists') {
    return null;
  }
  const id: unknown = error.problem?.details?.service_price_id;
  return typeof id === 'string' ? id : null;
}

// --------------------------------------------- the schedule-change dialog

export interface VersionFormValues {
  effectiveFrom: string;
  amount: string;
  notOffered: boolean;
}

export type VersionField = 'effectiveFrom' | 'amount';
export type VersionErrors = Partial<Record<VersionField, PriceMessage>>;

/**
 * The first date a new version may take (#89 E3): today, unless the item
 * has no versions (then its next version is a first one: any date).
 */
export function versionMinDate(
  versions: readonly PriceVersion[],
  today: LocalDate,
): LocalDate | undefined {
  return versions.length > 0 ? today : undefined;
}

/**
 * The suggested date: today, or the day after the latest version when that
 * one starts today or later.
 */
export function defaultVersionDate(
  versions: readonly PriceVersion[],
  today: LocalDate,
): LocalDate {
  const latest = versions.reduce<string | null>(
    (found, version) =>
      found === null || version.effective_from > found
        ? version.effective_from
        : found,
    null,
  );
  return latest !== null && latest >= today ? addDays(latest, 1) : today;
}

export function validateVersionForm(
  values: VersionFormValues,
  min: LocalDate | undefined,
): VersionErrors {
  const errors: VersionErrors = {};
  const date = dateError(values.effectiveFrom, min);
  if (date) errors.effectiveFrom = date;
  if (!values.notOffered) {
    const amount = amountError(values.amount);
    if (amount) errors.amount = amount;
  }
  return errors;
}

/** The POST body: `amount: null` means "not offered from this date". */
export function versionCreateBody(
  values: VersionFormValues,
): PriceVersionCreate {
  return {
    effective_from: values.effectiveFrom,
    amount: values.notOffered ? null : normalizeMoneyInput(values.amount),
  };
}

/** A 422's field errors on the version form, or null. */
export function versionServerErrors(error: unknown): VersionErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: VersionErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    if (where !== 'body') continue;
    const field =
      key === 'effective_from'
        ? 'effectiveFrom'
        : key === 'amount'
          ? 'amount'
          : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  // The rule's own code without a field error still belongs on the date.
  if (!errors.effectiveFrom && error.code === 'price_effective_from_invalid') {
    errors.effectiveFrom = serverMessage(error.code);
  }
  return Object.keys(errors).length > 0 ? errors : null;
}
