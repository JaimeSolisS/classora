import { ApiError } from '@/app/api';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import type {
  PayItem,
  PayItemCreate,
  PayItemDirection,
  PayItemRecurrence,
  PayItemType,
  PayItemTypeCreate,
} from './api';
import {
  isRealDay,
  serverErrors,
  serverMessage,
  ZERO,
  type PayrollMessage,
} from './rules';

// The client rules of the pay item dialogs (#454), without React. They only
// stop a request that would surely fail; the server stays authoritative and
// its refusals land on the field they are about. The amount stays a string,
// and no amount is computed here.

const V = 'payroll.validation';

/** The API's limits (#109). It counts code points, not UTF-16 units. */
const NAME_MAX = 100;
const DESCRIPTION_MAX = 500;

const textLength = (value: string) => Array.from(value).length;

export const PAY_ITEM_DIRECTIONS: readonly PayItemDirection[] = [
  'bonus',
  'deduction',
];
export const PAY_ITEM_RECURRENCES: readonly PayItemRecurrence[] = [
  'one_time',
  'recurring',
];

function hasCode(error: unknown, codes: readonly string[]): error is ApiError {
  return (
    error instanceof ApiError &&
    error.code !== undefined &&
    codes.includes(error.code)
  );
}

// ------------------------------------------------------------ the type dialog

export interface PayItemTypeFormValues {
  name: string;
  /** Only a new type picks one: it can't change later. */
  direction: PayItemDirection | '';
}

export type PayItemTypeField = keyof PayItemTypeFormValues;
export type PayItemTypeErrors = Partial<
  Record<PayItemTypeField, PayrollMessage>
>;

export function initialPayItemTypeForm(
  type: PayItemType | null,
): PayItemTypeFormValues {
  return { name: type?.name ?? '', direction: type?.direction ?? '' };
}

export function validatePayItemTypeForm(
  values: PayItemTypeFormValues,
): PayItemTypeErrors {
  const errors: PayItemTypeErrors = {};
  const name = values.name.trim();
  if (name === '') {
    errors.name = `${V}.nameRequired`;
  } else if (textLength(name) > NAME_MAX) {
    errors.name = `${V}.nameTooLong`;
  }
  if (!values.direction) {
    errors.direction = `${V}.directionRequired`;
  }
  return errors;
}

export function payItemTypeCreateBody(
  values: PayItemTypeFormValues,
): PayItemTypeCreate {
  return {
    name: values.name.trim(),
    direction: values.direction as PayItemDirection,
  };
}

export function payItemTypeServerErrors(
  error: unknown,
): PayItemTypeErrors | null {
  if (hasCode(error, ['pay_item_type_exists'])) {
    return { name: serverMessage('pay_item_type_exists') };
  }
  return serverErrors<PayItemTypeField>(error, {
    name: 'name',
    direction: 'direction',
  });
}

// ------------------------------------------------------------ the item dialog

export interface PayItemFormValues {
  typeId: string;
  description: string;
  /** As typed. */
  amount: string;
  recurrence: PayItemRecurrence;
  effectiveFrom: string;
  /** Recurring only; '' for no end. */
  effectiveTo: string;
}

export type PayItemField = keyof PayItemFormValues;
export type PayItemErrors = Partial<Record<PayItemField, PayrollMessage>>;

export function emptyPayItemForm(today: LocalDate): PayItemFormValues {
  return {
    typeId: '',
    description: '',
    amount: '',
    recurrence: 'one_time',
    effectiveFrom: today,
    effectiveTo: '',
  };
}

export function validatePayItemForm(values: PayItemFormValues): PayItemErrors {
  const errors: PayItemErrors = {};
  if (!values.typeId) {
    errors.typeId = `${V}.typeRequired`;
  }
  const description = values.description.trim();
  if (description === '') {
    errors.description = `${V}.descriptionRequired`;
  } else if (textLength(description) > DESCRIPTION_MAX) {
    errors.description = `${V}.descriptionTooLong`;
  }
  const amount = normalizeMoneyInput(values.amount);
  if (amount === '') {
    errors.amount = `${V}.amountRequired`;
  } else if (!isMoneyInput(amount)) {
    errors.amount = `${V}.amountInvalid`;
  } else if (ZERO.test(amount)) {
    errors.amount = `${V}.amountPositive`;
  }
  if (!isRealDay(values.effectiveFrom)) {
    errors.effectiveFrom = `${V}.dateRequired`;
  }
  if (values.recurrence === 'recurring' && values.effectiveTo !== '') {
    if (!isRealDay(values.effectiveTo)) {
      errors.effectiveTo = `${V}.dateRequired`;
    } else if (values.effectiveTo < values.effectiveFrom) {
      errors.effectiveTo = serverMessage('effective_to_before_from');
    }
  }
  return errors;
}

/** The POST body: `effective_to` goes only on a recurring item that has one. */
export function payItemCreateBody(values: PayItemFormValues): PayItemCreate {
  return {
    pay_item_type_id: values.typeId,
    description: values.description.trim(),
    amount: normalizeMoneyInput(values.amount),
    recurrence: values.recurrence,
    effective_from: values.effectiveFrom,
    ...(values.recurrence === 'recurring' && values.effectiveTo !== ''
      ? { effective_to: values.effectiveTo }
      : {}),
  };
}

/** The type on screen is out of date: refetch the list. */
export function isStaleType(error: unknown): boolean {
  return hasCode(error, ['pay_item_type_inactive', 'pay_item_type_unknown']);
}

export function payItemServerErrors(error: unknown): PayItemErrors | null {
  if (isStaleType(error)) {
    return { typeId: serverMessage((error as ApiError).code ?? '') };
  }
  const errors = serverErrors<PayItemField>(error, {
    pay_item_type_id: 'typeId',
    description: 'description',
    amount: 'amount',
    recurrence: 'recurrence',
    effective_from: 'effectiveFrom',
    effective_to: 'effectiveTo',
  });
  if (errors) {
    return errors;
  }
  // #109 D11: a lone surrogate is refused on the whole body, and the
  // description is the only free text in it.
  if (error instanceof ApiError && error.status === 422) {
    const invalid = error.fieldErrors.some(
      (item) => item.code === 'text_invalid' && item.path.length === 1,
    );
    if (invalid) {
      return { description: serverMessage('text_invalid') };
    }
  }
  return null;
}

// ------------------------------------------------------------- the end dialog

/** The date to start from: the current end, else today, never before the start. */
export function initialPayItemEnd(
  item: Pick<PayItem, 'effective_from' | 'effective_to'>,
  today: LocalDate,
): string {
  return (
    item.effective_to ??
    (today < item.effective_from ? item.effective_from : today)
  );
}

export function validatePayItemEnd(
  value: string,
  item: Pick<PayItem, 'effective_from'>,
): PayrollMessage | undefined {
  if (!isRealDay(value)) {
    return `${V}.dateRequired`;
  }
  return value < item.effective_from
    ? serverMessage('pay_item_end_before_start')
    : undefined;
}

/** A refusal about the date, as a message for its field. */
export function payItemEndServerError(
  error: unknown,
): PayrollMessage | undefined {
  if (
    hasCode(error, [
      'pay_item_end_before_paid_period',
      'pay_item_end_before_start',
    ])
  ) {
    return serverMessage(error.code ?? '');
  }
  return serverErrors<'date'>(error, { effective_to: 'date' })?.date;
}
