import { z } from '@/lib/forms';
import type {
  FamilyCreate,
  FamilyDetail,
  FamilyListParams,
  FamilyStatusFilter,
  FamilyUpdate,
} from './api';

// Pure helpers of the family pages (#172): the list's URL parameters, the
// name form's Zod schema (mirroring FamilyCreate / FamilyUpdate) and the
// request bodies. UX only: the API validates every request.

/** The API's limits (apps/api/app/modules/families). */
export const SEARCH_MAX_LENGTH = 100;
export const FAMILY_NAME_MAX_LENGTH = 200;

export const STATUS_FILTERS: readonly FamilyStatusFilter[] = [
  'active',
  'inactive',
  'all',
];

/** The list parameters in `search`; anything invalid is ignored. */
export function listParams(search: URLSearchParams): FamilyListParams {
  const status = search.get('status') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    status: (STATUS_FILTERS as readonly string[]).includes(status)
      ? (status as FamilyStatusFilter)
      : 'active',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

export type FamilyMessageKey =
  'families.validation.nameRequired' | 'families.validation.nameTooLong';

export interface FamilyFormValues {
  name: string;
}

export const EMPTY_FAMILY: FamilyFormValues = { name: '' };

export const familyFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'families.validation.nameRequired')
    .max(FAMILY_NAME_MAX_LENGTH, 'families.validation.nameTooLong'),
});

/** The form's values for an existing family. */
export function valuesOf(family: FamilyDetail): FamilyFormValues {
  return { name: family.name };
}

/** The `POST` body. */
export function createBody(values: FamilyFormValues): FamilyCreate {
  return { name: values.name };
}

/** The `PATCH` body: `version`, plus `name` when it changed. */
export function updateBody(
  family: FamilyDetail,
  values: FamilyFormValues,
): FamilyUpdate {
  const body: FamilyUpdate = { version: family.version };
  if (values.name !== family.name) {
    body.name = values.name;
  }
  return body;
}
