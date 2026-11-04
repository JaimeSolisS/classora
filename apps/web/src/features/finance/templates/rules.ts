import { ApiError } from '@/app/api';
import { canAccess } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';
import { apiCents, centsOf } from '@/features/billing/accounts/cents';
import { isCode, isUuid } from '@/features/payroll/periodRules';
import { isRealDay } from '@/features/payroll/rules';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import { addDays, type LocalDate } from '@/lib/time';
import type {
  ExpenseTemplate,
  ExpenseTemplateCreate,
  ExpenseTemplateUpdate,
  Frequency,
  TemplateListParams,
  TemplateStatus,
} from '../api';
import { fieldErrors, serverMessage, type FinanceMessage } from '../forms';

// The recurring template pages' rules (M7-13b), without React: the list's
// URL, who may do what, and the dialogs' fields, client checks and bodies.
// The checks mirror the API (#116 C1-C6, E5, E6); the API stays the
// authority and its refusals land on the field they are about.

const V = 'finance.templates.validation';

/** The API's limits (#116 C1). Names count code points once trimmed. */
export const TEMPLATE_NAME_MAX = 200;
export const DUE_OFFSET_MAX = 60;
/** The first period's window around the center's today (#116 E5). */
export const STARTS_ON_DAYS_BACK = 31;
export const STARTS_ON_DAYS_AHEAD = 366;

const textLength = (value: string) => Array.from(value.trim()).length;

/** One line: no control character (the API's `text_invalid`). */
function isOneLine(value: string): boolean {
  // eslint-disable-next-line no-control-regex
  return !/[\u0000-\u001f\u007f]/.test(value);
}

// ------------------------------------------------------------- the list

export const TEMPLATES_PATH = '/expenses/templates';
export const templatePath = (id: string) => `${TEMPLATES_PATH}/${id}`;

export const TEMPLATE_STATUSES: readonly TemplateStatus[] = [
  'active',
  'paused',
  'ended',
];
export const FREQUENCIES: readonly Frequency[] = [
  'weekly',
  'monthly',
  'annual',
];

/** The list's URL (B2): `status` (repeatable), `category`, `vendor`.
 * Anything invalid is ignored. */
export function templateListParams(
  search: URLSearchParams,
): TemplateListParams {
  const wanted = new Set(search.getAll('status'));
  const id = (name: string) => {
    const value = search.get(name) ?? '';
    return isUuid(value) ? value.toLowerCase() : '';
  };
  return {
    statuses: TEMPLATE_STATUSES.filter((status) => wanted.has(status)),
    categoryId: id('category'),
    vendorId: id('vendor'),
  };
}

export function templateListSearch(params: TemplateListParams): string {
  const next = new URLSearchParams();
  for (const status of params.statuses) next.append('status', status);
  if (params.categoryId) next.set('category', params.categoryId);
  if (params.vendorId) next.set('vendor', params.vendorId);
  return next.toString();
}

export function isTemplateFiltered(params: TemplateListParams): boolean {
  return (
    params.statuses.length > 0 ||
    params.categoryId !== '' ||
    params.vendorId !== ''
  );
}

/** The detail page's generated-expense page (`?page=`), 1-based. */
export function generatedPage(search: URLSearchParams): number {
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return Number.isInteger(page) && page >= 1 ? page : 1;
}

// ------------------------------------------------------------- who may

export interface TemplateCapabilities {
  /** "Nueva plantilla", "Editar", "Pausar", "Reanudar", "Finalizar" (A3). */
  manage: boolean;
}

export function templateCapabilities(
  user: SessionUser | null,
): TemplateCapabilities {
  return { manage: canAccess(user, { permission: 'expense.manage' }) };
}

/** What the detail page offers on this template (A3, D3, D4). */
export function templateActions(
  can: TemplateCapabilities,
  template: Pick<ExpenseTemplate, 'status'>,
) {
  return {
    edit: can.manage,
    pause: can.manage && template.status === 'active',
    resume: can.manage && template.status === 'paused',
    end: can.manage && template.status !== 'ended',
  };
}

// ------------------------------------------------------------- the form

export interface TemplateFormValues {
  name: string;
  categoryId: string;
  /** '' for none. */
  vendorId: string;
  amount: string;
  frequency: Frequency;
  startsOn: string;
  /** '' for no end. */
  endsOn: string;
  dueOffset: string;
}

export type TemplateField = keyof TemplateFormValues;
export type TemplateErrors = Partial<Record<TemplateField, FinanceMessage>>;

export const TEMPLATE_FIELD_ORDER: readonly TemplateField[] = [
  'name',
  'categoryId',
  'vendorId',
  'amount',
  'frequency',
  'startsOn',
  'endsOn',
  'dueOffset',
];

export function emptyTemplateForm(today: LocalDate): TemplateFormValues {
  return {
    name: '',
    categoryId: '',
    vendorId: '',
    amount: '',
    frequency: 'monthly',
    startsOn: today,
    endsOn: '',
    dueOffset: '0',
  };
}

export function templateForm(template: ExpenseTemplate): TemplateFormValues {
  return {
    name: template.name,
    categoryId: template.category.id,
    vendorId: template.vendor?.id ?? '',
    amount: template.amount,
    frequency: template.frequency,
    startsOn: template.starts_on,
    endsOn: template.ends_on ?? '',
    dueOffset: String(template.due_offset_days),
  };
}

/** The first period's allowed window (#116 E5). */
export function startsOnWindow(today: LocalDate) {
  return {
    min: addDays(today, -STARTS_ON_DAYS_BACK),
    max: addDays(today, STARTS_ON_DAYS_AHEAD),
  };
}

/** The earliest end an edit may set: the start, or the latest period
 * generated (#116 E6). */
export function earliestEnd(
  template: Pick<ExpenseTemplate, 'starts_on' | 'last_period_start'>,
): string {
  const last = template.last_period_start;
  return last && last > template.starts_on ? last : template.starts_on;
}

function amountError(value: string): FinanceMessage | undefined {
  const amount = normalizeMoneyInput(value);
  if (amount === '') return 'finance.expenses.validation.amountRequired';
  if (!isMoneyInput(amount)) return 'finance.expenses.validation.amountInvalid';
  const cents = centsOf(amount);
  if (cents === null || cents <= 0) {
    return 'finance.expenses.validation.amountPositive';
  }
  return undefined;
}

function dueOffsetOf(value: string): number | null {
  const trimmed = value.trim();
  if (!/^\d{1,2}$/.test(trimmed)) return null;
  const days = Number(trimmed);
  return days <= DUE_OFFSET_MAX ? days : null;
}

/** The end date's check: '' is no end; otherwise not before `min`. */
function endError(value: string, min: string): FinanceMessage | undefined {
  if (value === '') return undefined;
  if (!isRealDay(value)) return `${V}.dateRequired`;
  return value < min ? 'errors:expense_template_ends_on_invalid' : undefined;
}

/**
 * The client checks. `template` is the edited row (null on create): an edit
 * doesn't check the fixed frequency and start, and its end may not come
 * before the latest generated period.
 */
export function validateTemplateForm(
  values: TemplateFormValues,
  today: LocalDate,
  template: Pick<ExpenseTemplate, 'starts_on' | 'last_period_start'> | null,
): TemplateErrors {
  const errors: TemplateErrors = {};
  const name = textLength(values.name);
  if (name === 0) {
    errors.name = `${V}.nameRequired`;
  } else if (name > TEMPLATE_NAME_MAX) {
    errors.name = `${V}.nameTooLong`;
  } else if (!isOneLine(values.name.trim())) {
    errors.name = 'errors:text_invalid';
  }
  if (values.categoryId === '') errors.categoryId = `${V}.categoryRequired`;
  const amount = amountError(values.amount);
  if (amount) errors.amount = amount;
  if (dueOffsetOf(values.dueOffset) === null) {
    errors.dueOffset = `${V}.dueOffsetRange`;
  }
  if (template === null) {
    const window = startsOnWindow(today);
    if (!isRealDay(values.startsOn)) {
      errors.startsOn = `${V}.dateRequired`;
    } else if (values.startsOn < window.min || values.startsOn > window.max) {
      errors.startsOn = 'errors:expense_template_starts_on_invalid';
    }
    const end = endError(
      values.endsOn,
      isRealDay(values.startsOn) ? values.startsOn : '',
    );
    if (end) errors.endsOn = end;
  } else {
    const end = endError(values.endsOn, earliestEnd(template));
    if (end) errors.endsOn = end;
  }
  return errors;
}

export function templateCreateBody(
  values: TemplateFormValues,
): ExpenseTemplateCreate {
  return {
    name: values.name.trim(),
    category_id: values.categoryId,
    vendor_id: values.vendorId === '' ? null : values.vendorId,
    amount: normalizeMoneyInput(values.amount),
    frequency: values.frequency,
    starts_on: values.startsOn,
    ends_on: values.endsOn === '' ? null : values.endsOn,
    due_offset_days: dueOffsetOf(values.dueOffset) ?? 0,
  };
}

/**
 * The PATCH body of an edit (D2): only what changed, with `vendor_id: null`
 * and `ends_on: null` when cleared; null when nothing changed (nothing is
 * sent). Frequency and start are never sent (#116 E4).
 */
export function templateUpdateBody(
  values: TemplateFormValues,
  base: ExpenseTemplate,
): ExpenseTemplateUpdate | null {
  const body: ExpenseTemplateUpdate = { version: base.version };
  const name = values.name.trim();
  if (name !== base.name) body.name = name;
  if (values.categoryId !== base.category.id) {
    body.category_id = values.categoryId;
  }
  const vendorId = values.vendorId === '' ? null : values.vendorId;
  if (vendorId !== (base.vendor?.id ?? null)) body.vendor_id = vendorId;
  const amount = normalizeMoneyInput(values.amount);
  if (centsOf(amount) !== apiCents(base.amount)) body.amount = amount;
  const dueOffset = dueOffsetOf(values.dueOffset);
  if (dueOffset !== null && dueOffset !== base.due_offset_days) {
    body.due_offset_days = dueOffset;
  }
  const endsOn = values.endsOn === '' ? null : values.endsOn;
  if (endsOn !== base.ends_on) body.ends_on = endsOn;
  return Object.keys(body).length > 1 ? body : null;
}

/** A refusal on the form's fields (D5), or null for the alert. */
export function templateServerErrors(error: unknown): TemplateErrors | null {
  if (isCode(error, 'expense_template_exists')) {
    return { name: serverMessage('expense_template_exists') };
  }
  if (isCode(error, 'expense_category_unknown', 'expense_category_inactive')) {
    return { categoryId: serverMessage((error as ApiError).code) };
  }
  if (isCode(error, 'vendor_unknown', 'vendor_inactive')) {
    return { vendorId: serverMessage((error as ApiError).code) };
  }
  const found = fieldErrors<TemplateField>(error, {
    name: 'name',
    category_id: 'categoryId',
    vendor_id: 'vendorId',
    amount: 'amount',
    frequency: 'frequency',
    starts_on: 'startsOn',
    ends_on: 'endsOn',
    due_offset_days: 'dueOffset',
  });
  if (found) return found;
  if (isCode(error, 'text_invalid')) {
    // A lone surrogate is refused before the body is read.
    return { name: serverMessage('text_invalid') };
  }
  return null;
}

/** The refusals after which the category and vendor pickers reload. */
export const RELOAD_PICKERS_AFTER: readonly string[] = [
  'expense_category_unknown',
  'expense_category_inactive',
  'vendor_unknown',
  'vendor_inactive',
];

// ------------------------------------------------------------- end

export interface EndFormValues {
  endsOn: string;
}

/** "Finalizar" defaults to today, or the earliest allowed end (D4). */
export function initialEnd(
  template: Pick<ExpenseTemplate, 'starts_on' | 'last_period_start'>,
  today: LocalDate,
): EndFormValues {
  const min = earliestEnd(template);
  return { endsOn: today < min ? min : today };
}

export function validateEnd(
  values: EndFormValues,
  template: Pick<ExpenseTemplate, 'starts_on' | 'last_period_start'>,
): Partial<Record<'endsOn', FinanceMessage>> {
  if (values.endsOn === '') return { endsOn: `${V}.dateRequired` };
  const error = endError(values.endsOn, earliestEnd(template));
  return error ? { endsOn: error } : {};
}

export function endServerErrors(
  error: unknown,
): Partial<Record<'endsOn', FinanceMessage>> | null {
  if (error instanceof ApiError && error.status === 422) {
    const found = fieldErrors<'endsOn'>(error, { ends_on: 'endsOn' });
    if (found) return found;
  }
  return null;
}
