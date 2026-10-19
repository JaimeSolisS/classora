import { z } from '@/lib/forms';
import type {
  GuardianAccount,
  GuardianCreate,
  GuardianDetail,
  GuardianListParams,
  GuardianRelationship,
  GuardianStatusFilter,
  GuardianUpdate,
} from './api';

// Pure helpers of the guardian pages (#34): the list's URL parameters, the
// form's values and Zod schema (mirroring GuardianCreate / GuardianUpdate)
// and the request bodies. UX only: the API validates every request.

/** The API's limits (apps/api/app/modules/families). */
export const SEARCH_MAX_LENGTH = 100;
export const NAME_MAX_LENGTH = 100;
export const EMAIL_MAX_LENGTH = 254;
export const PHONE_MAX_LENGTH = 32;
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9 ().-]{5,30}[0-9]$/;

export const STATUS_FILTERS: readonly GuardianStatusFilter[] = [
  'active',
  'inactive',
  'all',
];

/** The relationship codes, in the API's order (GuardianRelationship). */
export const RELATIONSHIPS: readonly GuardianRelationship[] = [
  'mother',
  'father',
  'grandparent',
  'stepparent',
  'sibling',
  'other_relative',
  'legal_guardian',
  'other',
];

/** The list parameters in `search`; anything invalid is ignored. */
export function listParams(search: URLSearchParams): GuardianListParams {
  const status = search.get('status') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    status: (STATUS_FILTERS as readonly string[]).includes(status)
      ? (status as GuardianStatusFilter)
      : 'active',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

export type GuardianMessageKey =
  | 'guardians.validation.nameRequired'
  | 'guardians.validation.nameTooLong'
  | 'guardians.validation.emailInvalid'
  | 'guardians.validation.phoneInvalid';

export interface GuardianFormValues {
  givenNames: string;
  familyNames: string;
  email: string;
  phone: string;
  /** The linked login account, or null. */
  account: GuardianAccount | null;
}

export const EMPTY_GUARDIAN: GuardianFormValues = {
  givenNames: '',
  familyNames: '',
  email: '',
  phone: '',
  account: null,
};

const required = () =>
  z
    .string()
    .trim()
    .min(1, 'guardians.validation.nameRequired')
    .max(NAME_MAX_LENGTH, 'guardians.validation.nameTooLong');

const optionalPattern = (
  pattern: RegExp,
  max: number,
  message: GuardianMessageKey,
) =>
  z
    .string()
    .trim()
    .refine(
      (value) => value === '' || (value.length <= max && pattern.test(value)),
      message,
    );

export const guardianFormSchema = z.object({
  givenNames: required(),
  familyNames: required(),
  email: optionalPattern(
    EMAIL_PATTERN,
    EMAIL_MAX_LENGTH,
    'guardians.validation.emailInvalid',
  ),
  phone: optionalPattern(
    PHONE_PATTERN,
    PHONE_MAX_LENGTH,
    'guardians.validation.phoneInvalid',
  ),
  account: z
    .object({
      id: z.string(),
      username: z.string(),
      display_name: z.string(),
      state: z.enum(['pending', 'active', 'suspended', 'archived']),
    })
    .nullable(),
});

/** The form's values for an existing guardian (as a family.manage holder). */
export function valuesOf(guardian: GuardianDetail): GuardianFormValues {
  return {
    givenNames: guardian.given_names,
    familyNames: guardian.family_names,
    email: guardian.email ?? '',
    phone: guardian.phone ?? '',
    account: guardian.account,
  };
}

const orNull = (value: string) => (value === '' ? null : value);

/** The `POST` body; empty optional fields are left out. */
export function createBody(values: GuardianFormValues): GuardianCreate {
  const body: GuardianCreate = {
    given_names: values.givenNames,
    family_names: values.familyNames,
  };
  if (values.email) body.email = values.email;
  if (values.phone) body.phone = values.phone;
  if (values.account) body.user_id = values.account.id;
  return body;
}

/**
 * The `PATCH` body: `version` plus the fields that differ from `guardian`.
 * Removing the account sends `user_id: null`.
 */
export function updateBody(
  guardian: GuardianDetail,
  values: GuardianFormValues,
): GuardianUpdate {
  const before = valuesOf(guardian);
  const body: GuardianUpdate = { version: guardian.version };
  if (values.givenNames !== before.givenNames) {
    body.given_names = values.givenNames;
  }
  if (values.familyNames !== before.familyNames) {
    body.family_names = values.familyNames;
  }
  if (values.email !== before.email) body.email = orNull(values.email);
  if (values.phone !== before.phone) body.phone = orNull(values.phone);
  const accountBefore = guardian.account?.id ?? null;
  const accountAfter = values.account?.id ?? null;
  // A profile with an account the viewer can't see (has_account without
  // `account`) never reaches the form: only family.manage holders edit.
  if (accountAfter !== accountBefore) body.user_id = accountAfter;
  return body;
}

/** "Family names, Given names" (the list's name column). */
export function listName(person: {
  given_names: string;
  family_names: string;
}): string {
  return `${person.family_names}, ${person.given_names}`;
}

/** "Given Family" (headings and links). */
export function fullName(person: {
  given_names: string;
  family_names: string;
}): string {
  return `${person.given_names} ${person.family_names}`;
}
