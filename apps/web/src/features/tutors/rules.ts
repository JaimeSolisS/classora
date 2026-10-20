import { USER_STATES } from '@/features/users/admin/api';
import type {
  TutorDetail,
  TutorField,
  TutorListParams,
  TutorUpdate,
  UserState,
} from './api';

// Pure helpers of the tutor pages (#43): the list's URL parameters and the
// profile change body. UX only: the server's `editable_fields` and the route
// decide.

/** The API's limit on `q`. */
export const SEARCH_MAX_LENGTH = 100;
/** The API's limit on the professional experience. */
export const EXPERIENCE_MAX_LENGTH = 4000;

/** The list parameters in `search`; anything invalid is ignored. */
export function listParams(search: URLSearchParams): TutorListParams {
  const state = search.get('state') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    state: (USER_STATES as readonly string[]).includes(state)
      ? (state as UserState)
      : '',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** The form's values (trimmed by the schema). */
export interface TutorValues {
  displayName: string;
  email: string;
  phone: string;
  professionalExperience: string;
}

export function valuesOf(tutor: TutorDetail): TutorValues {
  return {
    displayName: tutor.display_name,
    email: tutor.email ?? '',
    phone: tutor.phone ?? '',
    professionalExperience: tutor.professional_experience ?? '',
  };
}

/**
 * `version` plus the editable fields that differ from `tutor` (empty clears
 * an optional field). Fields outside `tutor.editable_fields` are never sent.
 */
export function tutorChanges(
  tutor: TutorDetail,
  values: TutorValues,
): TutorUpdate {
  const editable = new Set<TutorField>(tutor.editable_fields);
  const body: TutorUpdate = { version: tutor.version };
  const optional = (value: string) => (value === '' ? null : value);
  if (
    editable.has('display_name') &&
    values.displayName !== tutor.display_name
  ) {
    body.display_name = values.displayName;
  }
  if (editable.has('email') && values.email !== (tutor.email ?? '')) {
    body.email = optional(values.email);
  }
  if (editable.has('phone') && values.phone !== (tutor.phone ?? '')) {
    body.phone = optional(values.phone);
  }
  if (
    editable.has('professional_experience') &&
    values.professionalExperience !== (tutor.professional_experience ?? '')
  ) {
    body.professional_experience = optional(values.professionalExperience);
  }
  return body;
}
