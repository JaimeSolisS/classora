import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { SEARCH_MAX_LENGTH } from '@/features/tutors/rules';
import { USER_STATES } from '@/features/users/admin/api';
import { isMoneyInput, isRateInput, normalizeMoneyInput } from '@/lib/money';
import { isoWeekday, parseLocalDate, type LocalDate } from '@/lib/time';
import type {
  ClassKind,
  CompensationRateCreate,
  CompensationStatus,
  PayFrequency,
  PayrollSettings,
  PayrollSettingsUpdate,
  PayrollTutorListParams,
  PayTermsCreate,
  ProrationBasis,
  RateModel,
  TutorCompensation,
  UserState,
} from './api';

// The pay pages' client rules (#425), without React: the list's URL
// parameters, the order of a tutor's versions, the model summary, the two
// version dialogs and the settings form. The server stays authoritative:
// these only stop a request that would surely fail, and no amount is ever
// computed here (C3).

export { SEARCH_MAX_LENGTH };

export const PAY_FREQUENCIES: readonly PayFrequency[] = [
  'weekly',
  'biweekly',
  'monthly',
];
export const CLASS_KINDS: readonly ClassKind[] = [
  'individual',
  'group',
  'course',
];
export const PRORATION_BASES: readonly ProrationBasis[] = [
  'calendar_days',
  'weekdays',
];

const RATE_MODELS: readonly RateModel[] = ['per_hour', 'per_session'];

/** The models a class kind takes: `per_course` is for courses only (C6). */
export function modelsFor(kind: ClassKind | ''): readonly RateModel[] {
  return kind === 'course' ? [...RATE_MODELS, 'per_course'] : RATE_MODELS;
}

/** A `common` key, or an `errors` key prefixed with `errors:`. */
export type PayrollMessage = string;

const V = 'payroll.validation';

// ---------------------------------------------------------- the tutor list

export const DEFAULT_LIST_STATE: UserState = 'active';

/** The list parameters in the URL; anything invalid is ignored (B1). */
export function payrollListParams(
  search: URLSearchParams,
): PayrollTutorListParams {
  const state = search.get('state') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    state:
      state === 'all'
        ? 'all'
        : (USER_STATES.find((item) => item === state) ?? DEFAULT_LIST_STATE),
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

// ---------------------------------------------------- a tutor's versions

const STATUS_ORDER: Record<CompensationStatus, number> = {
  current: 0,
  upcoming: 1,
  past: 2,
};

/**
 * The display order (C1): the current version, the upcoming ones (the next
 * change first), then the past ones (the latest first).
 */
export function orderVersions<
  T extends { status: CompensationStatus; effective_from: string },
>(versions: readonly T[]): T[] {
  return [...versions].sort(
    (a, b) =>
      STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
      (a.status === 'upcoming'
        ? a.effective_from.localeCompare(b.effective_from)
        : b.effective_from.localeCompare(a.effective_from)),
  );
}

/** Whether a salary is in effect today. */
export function hasCurrentSalary(
  compensation: Pick<TutorCompensation, 'terms'>,
): boolean {
  return compensation.terms.some(
    (terms) => terms.status === 'current' && terms.salary_amount !== null,
  );
}

export type PaySummaryKey = RateModel | 'salary' | 'salaryPlus';

/**
 * The words for the current pay model (C3), from today's data only: a salary
 * with or without extras, otherwise each model in use. Empty when nothing
 * is in effect or the tutor is off payroll.
 */
export function paySummary(
  compensation: Pick<TutorCompensation, 'terms' | 'rates'>,
): PaySummaryKey[] {
  const terms = compensation.terms.find((item) => item.status === 'current');
  if (terms && terms.pay_frequency === null) {
    return [];
  }
  const models = compensation.rates.flatMap((rate) =>
    rate.status === 'current' && rate.rate !== null && rate.rate_model !== null
      ? [rate.rate_model]
      : [],
  );
  if (terms?.salary_amount != null) {
    return [models.length > 0 ? 'salaryPlus' : 'salary'];
  }
  return [...new Set(models)];
}

// ---------------------------------------------------------- shared checks

export function isRealDay(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

function serverMessage(code: string): PayrollMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/** The rule codes that come without a field and belong on the date. */
const DATE_RULES: readonly string[] = [
  'effective_from_inside_generated_period',
  'effective_from_before_first_period',
];

/**
 * A 422's field errors on a form's fields (the first per field), plus the
 * payroll date rules on `dateField`; null when none is about the form.
 */
function serverErrors<F extends string>(
  error: unknown,
  fields: Readonly<Record<string, F>>,
  dateField?: F,
): Partial<Record<F, PayrollMessage>> | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: Partial<Record<F, PayrollMessage>> = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? fields[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  if (dateField && error.code && DATE_RULES.includes(error.code)) {
    errors[dateField] = serverMessage(error.code);
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

// ------------------------------------------------------- the terms dialog

export interface TermsFormValues {
  effectiveFrom: string;
  /** "Stop payroll from this date": no frequency and no salary. */
  stop: boolean;
  frequency: PayFrequency | '';
  /** As typed; '' for no salary. */
  salary: string;
}

export type TermsField = 'effectiveFrom' | 'frequency' | 'salary';
export type TermsErrors = Partial<Record<TermsField, PayrollMessage>>;

export function emptyTermsForm(today: LocalDate): TermsFormValues {
  return { effectiveFrom: today, stop: false, frequency: '', salary: '' };
}

const ZERO = /^0+(\.0+)?$/;

export function validateTermsForm(values: TermsFormValues): TermsErrors {
  const errors: TermsErrors = {};
  if (!isRealDay(values.effectiveFrom)) {
    errors.effectiveFrom = `${V}.dateRequired`;
  }
  if (values.stop) {
    return errors;
  }
  if (!values.frequency) {
    errors.frequency = `${V}.frequencyRequired`;
  }
  const salary = normalizeMoneyInput(values.salary);
  if (salary !== '' && !isMoneyInput(salary)) {
    errors.salary = `${V}.salaryInvalid`;
  } else if (ZERO.test(salary)) {
    errors.salary = `${V}.salaryPositive`;
  }
  return errors;
}

/** The POST body; a stop sends `null` for both, the salary stays a string. */
export function termsCreateBody(values: TermsFormValues): PayTermsCreate {
  const salary = normalizeMoneyInput(values.salary);
  return {
    effective_from: values.effectiveFrom,
    pay_frequency: values.stop || !values.frequency ? null : values.frequency,
    salary_amount: values.stop || salary === '' ? null : salary,
  };
}

export function termsServerErrors(error: unknown): TermsErrors | null {
  return serverErrors<TermsField>(
    error,
    {
      effective_from: 'effectiveFrom',
      pay_frequency: 'frequency',
      salary_amount: 'salary',
    },
    'effectiveFrom',
  );
}

// -------------------------------------------------------- the rate dialog

export interface RateFormValues {
  classKind: ClassKind | '';
  effectiveFrom: string;
  /** "No rate from this date": no model and no rate. */
  noRate: boolean;
  model: RateModel | '';
  /** As typed. */
  rate: string;
}

export type RateField = 'classKind' | 'effectiveFrom' | 'model' | 'rate';
export type RateErrors = Partial<Record<RateField, PayrollMessage>>;

export function emptyRateForm(today: LocalDate): RateFormValues {
  return {
    classKind: '',
    effectiveFrom: today,
    noRate: false,
    model: '',
    rate: '',
  };
}

/** The values with another class kind: a model it doesn't take is cleared. */
export function changeRateKind(
  values: RateFormValues,
  classKind: ClassKind | '',
): RateFormValues {
  const keeps =
    values.model !== '' && modelsFor(classKind).includes(values.model);
  return { ...values, classKind, model: keeps ? values.model : '' };
}

export function validateRateForm(values: RateFormValues): RateErrors {
  const errors: RateErrors = {};
  if (!values.classKind) {
    errors.classKind = `${V}.classKindRequired`;
  }
  if (!isRealDay(values.effectiveFrom)) {
    errors.effectiveFrom = `${V}.dateRequired`;
  }
  if (values.noRate) {
    return errors;
  }
  if (!values.model || !modelsFor(values.classKind).includes(values.model)) {
    errors.model = `${V}.modelRequired`;
  }
  const rate = normalizeMoneyInput(values.rate);
  if (rate === '') {
    errors.rate = `${V}.rateRequired`;
  } else if (!isRateInput(rate)) {
    errors.rate = `${V}.rateInvalid`;
  }
  return errors;
}

/** The POST body; "no rate" sends `null` for both, the rate stays a string. */
export function rateCreateBody(values: RateFormValues): CompensationRateCreate {
  return {
    class_kind: values.classKind as ClassKind,
    effective_from: values.effectiveFrom,
    rate_model: values.noRate || !values.model ? null : values.model,
    rate: values.noRate ? null : normalizeMoneyInput(values.rate),
  };
}

export function rateServerErrors(error: unknown): RateErrors | null {
  return serverErrors<RateField>(
    error,
    {
      class_kind: 'classKind',
      effective_from: 'effectiveFrom',
      rate_model: 'model',
      rate: 'rate',
    },
    'effectiveFrom',
  );
}

// ---------------------------------------------------- the settings dialog

export interface SettingsFormValues {
  /** `YYYY-MM-DD`; empty keeps payroll off. */
  startsOn: string;
  anchor: string;
  basis: ProrationBasis;
}

export type SettingsField = keyof SettingsFormValues;
export type SettingsErrors = Partial<Record<SettingsField, PayrollMessage>>;

export const MIN_SETTINGS_DATE = '2000-01-01';
export const MAX_SETTINGS_DATE = '2099-12-31';

export function initialSettingsForm(
  settings: PayrollSettings,
): SettingsFormValues {
  return {
    startsOn: settings.payroll_starts_on ?? '',
    anchor: settings.biweekly_anchor_on,
    basis: settings.salary_proration_basis,
  };
}

function inSettingsRange(value: string): boolean {
  return (
    isRealDay(value) && value >= MIN_SETTINGS_DATE && value <= MAX_SETTINGS_DATE
  );
}

/** D3: the only rule of the page's own is the Monday anchor. A locked field
 * isn't checked: the API's value goes back unchanged. */
export function validateSettingsForm(
  values: SettingsFormValues,
  settings: Pick<PayrollSettings, 'start_locked' | 'anchor_locked'>,
): SettingsErrors {
  const errors: SettingsErrors = {};
  if (
    !settings.start_locked &&
    values.startsOn !== '' &&
    !inSettingsRange(values.startsOn)
  ) {
    errors.startsOn = `${V}.dateRange`;
  }
  if (!settings.anchor_locked) {
    if (!inSettingsRange(values.anchor)) {
      errors.anchor = `${V}.dateRange`;
    } else if (isoWeekday(values.anchor) !== 1) {
      errors.anchor = 'errors:anchor_not_monday';
    }
  }
  return errors;
}

/**
 * Whether to warn that a date can't change after the next nightly run (the
 * #106 addendum): a date is about to be saved and periods don't exist yet.
 */
export function warnsAboutStart(
  values: SettingsFormValues,
  settings: Pick<PayrollSettings, 'start_locked'>,
): boolean {
  return !settings.start_locked && values.startsOn !== '';
}

/** The PATCH body: every key, with the API's value for a locked field. */
export function settingsUpdateBody(
  values: SettingsFormValues,
  settings: PayrollSettings,
): PayrollSettingsUpdate {
  return {
    version: settings.version,
    payroll_starts_on: settings.start_locked
      ? settings.payroll_starts_on
      : values.startsOn || null,
    biweekly_anchor_on: settings.anchor_locked
      ? settings.biweekly_anchor_on
      : values.anchor,
    salary_proration_basis: values.basis,
  };
}

export function settingsServerErrors(error: unknown): SettingsErrors | null {
  return serverErrors<SettingsField>(error, {
    payroll_starts_on: 'startsOn',
    biweekly_anchor_on: 'anchor',
    salary_proration_basis: 'basis',
  });
}

export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}

/** The refusals that mean the shown settings are stale: reload them. */
export function isStaleSettings(error: unknown): boolean {
  return (
    isVersionConflict(error) ||
    (error instanceof ApiError &&
      (error.code === 'payroll_start_locked' ||
        error.code === 'biweekly_anchor_locked'))
  );
}
