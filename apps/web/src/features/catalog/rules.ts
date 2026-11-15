import type {
  EducationLevel,
  EducationLevelCreate,
  EducationLevelUpdate,
  Subject,
  SubjectUpdate,
} from './api';

// Pure helpers of the catalog forms (#32 H3, I2): the request bodies from
// the form values. An update sends the loaded `version` plus only the fields
// that changed; null means nothing changed (no request).

/** The F-bounds of the API (models.NAME_MAX_LENGTH, SORT_ORDER_MAX). */
export const NAME_MAX_LENGTH = 100;
export const POSITION_MAX = 9999;

export interface CatalogFormValues {
  /** Trimmed name. */
  name: string;
  /** The position (levels only); null when left empty. */
  position: number | null;
}

export function levelCreateBody(
  values: CatalogFormValues,
): EducationLevelCreate {
  return values.position === null
    ? { name: values.name }
    : { name: values.name, sort_order: values.position };
}

export function levelUpdateBody(
  level: EducationLevel,
  values: CatalogFormValues,
): EducationLevelUpdate | null {
  const body: EducationLevelUpdate = { version: level.version };
  if (values.name !== level.name) {
    body.name = values.name;
  }
  if (values.position !== null && values.position !== level.sort_order) {
    body.sort_order = values.position;
  }
  return Object.keys(body).length > 1 ? body : null;
}

export function subjectUpdateBody(
  subject: Subject,
  values: CatalogFormValues,
): SubjectUpdate | null {
  return values.name === subject.name
    ? null
    : { version: subject.version, name: values.name };
}

/** 1-based page from the `page` search parameter (anything else: 1). */
export function pageParam(search: URLSearchParams): number {
  const raw = search.get('page');
  const page = raw && /^[1-9][0-9]{0,5}$/.test(raw) ? Number(raw) : 1;
  return page;
}
