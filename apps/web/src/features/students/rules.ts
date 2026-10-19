import { z } from '@/lib/forms';
import type {
  EmergencyContactUpdate,
  StudentCreate,
  StudentDetail,
  StudentListParams,
  StudentStatusFilter,
  StudentUpdate,
} from './api';

// Pure helpers of the student pages (#33): the list's URL parameters, the
// form's values and Zod schema (mirroring StudentCreate / StudentUpdate) and
// the request bodies. UX only: the API validates every request.

/** The API's limits (apps/api/app/modules/families). */
export const SEARCH_MAX_LENGTH = 100;
export const NAME_MAX_LENGTH = 100;
export const EMAIL_MAX_LENGTH = 254;
export const PHONE_MAX_LENGTH = 32;
export const SCHOOL_MAX_LENGTH = 200;
export const GRADE_MAX_LENGTH = 50;
export const CONTACT_NAME_MAX_LENGTH = 200;
export const RELATIONSHIP_MAX_LENGTH = 50;
export const MAX_CONTACTS = 5;
export const EARLIEST_BIRTHDATE = '1900-01-01';
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9 ().-]{5,30}[0-9]$/;
const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;

export const STATUS_FILTERS: readonly StudentStatusFilter[] = [
  'active',
  'inactive',
  'all',
];

/** The list parameters in `search`; anything invalid is ignored. */
export function listParams(search: URLSearchParams): StudentListParams {
  const status = search.get('status') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    level: search.get('level') ?? '',
    status: (STATUS_FILTERS as readonly string[]).includes(status)
      ? (status as StudentStatusFilter)
      : 'active',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

export type StudentMessageKey =
  | 'students.validation.nameRequired'
  | 'students.validation.nameTooLong'
  | 'students.validation.birthdateRequired'
  | 'students.validation.birthdateRange'
  | 'students.validation.emailInvalid'
  | 'students.validation.phoneInvalid'
  | 'students.validation.schoolTooLong'
  | 'students.validation.gradeTooLong'
  | 'students.validation.contactNameTooLong'
  | 'students.validation.relationshipTooLong';

/** One emergency contact row of the form. */
export interface ContactValues {
  /** The contact's id when it already exists ('' for a new one). */
  contactId: string;
  name: string;
  relationship: string;
  phone: string;
}

export interface StudentFormValues {
  givenNames: string;
  familyNames: string;
  /** `YYYY-MM-DD` (an `<input type="date">` value) or ''. */
  birthdate: string;
  email: string;
  phone: string;
  school: string;
  grade: string;
  /** An education level id, or '' for none. */
  levelId: string;
  contacts: ContactValues[];
}

export const EMPTY_CONTACT: ContactValues = {
  contactId: '',
  name: '',
  relationship: '',
  phone: '',
};

export const EMPTY_STUDENT: StudentFormValues = {
  givenNames: '',
  familyNames: '',
  birthdate: '',
  email: '',
  phone: '',
  school: '',
  grade: '',
  levelId: '',
  contacts: [EMPTY_CONTACT],
};

const required = (max: number, tooLong: StudentMessageKey) =>
  z
    .string()
    .trim()
    .min(1, 'students.validation.nameRequired')
    .max(max, tooLong);

const optional = (max: number, tooLong: StudentMessageKey) =>
  z.string().trim().max(max, tooLong);

const optionalPattern = (
  pattern: RegExp,
  max: number,
  message: StudentMessageKey,
) =>
  z
    .string()
    .trim()
    .refine(
      (value) => value === '' || (value.length <= max && pattern.test(value)),
      message,
    );

/** The form schema; `today` (`YYYY-MM-DD`, center zone) bounds the birthdate. */
export function studentFormSchema(today: string) {
  return z.object({
    givenNames: required(NAME_MAX_LENGTH, 'students.validation.nameTooLong'),
    familyNames: required(NAME_MAX_LENGTH, 'students.validation.nameTooLong'),
    birthdate: z
      .string()
      .min(1, 'students.validation.birthdateRequired')
      .refine(
        (value) =>
          LOCAL_DATE.test(value) &&
          value >= EARLIEST_BIRTHDATE &&
          value <= today,
        'students.validation.birthdateRange',
      ),
    email: optionalPattern(
      EMAIL_PATTERN,
      EMAIL_MAX_LENGTH,
      'students.validation.emailInvalid',
    ),
    phone: optionalPattern(
      PHONE_PATTERN,
      PHONE_MAX_LENGTH,
      'students.validation.phoneInvalid',
    ),
    school: optional(SCHOOL_MAX_LENGTH, 'students.validation.schoolTooLong'),
    grade: optional(GRADE_MAX_LENGTH, 'students.validation.gradeTooLong'),
    levelId: z.string(),
    contacts: z
      .array(
        z.object({
          contactId: z.string(),
          name: required(
            CONTACT_NAME_MAX_LENGTH,
            'students.validation.contactNameTooLong',
          ),
          relationship: required(
            RELATIONSHIP_MAX_LENGTH,
            'students.validation.relationshipTooLong',
          ),
          phone: z
            .string()
            .trim()
            .refine(
              (value) =>
                value.length <= PHONE_MAX_LENGTH && PHONE_PATTERN.test(value),
              'students.validation.phoneInvalid',
            ),
        }),
      )
      .min(1)
      .max(MAX_CONTACTS),
  });
}

/** The form's values for an existing student (full access). */
export function valuesOf(student: StudentDetail): StudentFormValues {
  const personal = student.personal;
  return {
    givenNames: student.given_names,
    familyNames: student.family_names,
    birthdate: personal?.birthdate ?? '',
    email: personal?.email ?? '',
    phone: personal?.phone ?? '',
    school: student.school_name ?? '',
    grade: student.school_grade ?? '',
    levelId: student.education_level?.id ?? '',
    contacts: (personal?.emergency_contacts ?? []).map((contact) => ({
      contactId: contact.id,
      name: contact.name,
      relationship: contact.relationship,
      phone: contact.phone,
    })),
  };
}

const orNull = (value: string) => (value === '' ? null : value);

/** The `POST` body; empty optional fields are left out. */
export function createBody(values: StudentFormValues): StudentCreate {
  const body: StudentCreate = {
    given_names: values.givenNames,
    family_names: values.familyNames,
    birthdate: values.birthdate,
    emergency_contacts: values.contacts.map(
      ({ name, relationship, phone }) => ({
        name,
        relationship,
        phone,
      }),
    ),
  };
  if (values.email) body.email = values.email;
  if (values.phone) body.phone = values.phone;
  if (values.school) body.school_name = values.school;
  if (values.grade) body.school_grade = values.grade;
  if (values.levelId) body.education_level_id = values.levelId;
  return body;
}

function contactItems(values: StudentFormValues): EmergencyContactUpdate[] {
  return values.contacts.map(({ contactId, name, relationship, phone }) => ({
    ...(contactId ? { id: contactId } : {}),
    name,
    relationship,
    phone,
  }));
}

/**
 * Whether any contact field, the number of contacts or their order differs
 * from the loaded student.
 */
export function contactsChanged(
  student: StudentDetail,
  values: StudentFormValues,
): boolean {
  const before = valuesOf(student).contacts;
  const after = values.contacts;
  return (
    before.length !== after.length ||
    before.some(
      (contact, index) =>
        contact.contactId !== after[index]?.contactId ||
        contact.name !== after[index].name ||
        contact.relationship !== after[index].relationship ||
        contact.phone !== after[index].phone,
    )
  );
}

/**
 * The `PATCH` body: `version` plus the fields that differ from `student`;
 * `emergency_contacts` (the full list with ids) only when contacts changed.
 */
export function updateBody(
  student: StudentDetail,
  values: StudentFormValues,
): StudentUpdate {
  const before = valuesOf(student);
  const body: StudentUpdate = { version: student.version };
  if (values.givenNames !== before.givenNames) {
    body.given_names = values.givenNames;
  }
  if (values.familyNames !== before.familyNames) {
    body.family_names = values.familyNames;
  }
  if (values.birthdate !== before.birthdate) {
    body.birthdate = values.birthdate;
  }
  if (values.email !== before.email) body.email = orNull(values.email);
  if (values.phone !== before.phone) body.phone = orNull(values.phone);
  if (values.school !== before.school) body.school_name = orNull(values.school);
  if (values.grade !== before.grade) body.school_grade = orNull(values.grade);
  if (values.levelId !== before.levelId) {
    body.education_level_id = orNull(values.levelId);
  }
  if (contactsChanged(student, values)) {
    body.emergency_contacts = contactItems(values);
  }
  return body;
}

/** "Family names, Given names" (the list's name column). */
export function listName(student: {
  given_names: string;
  family_names: string;
}): string {
  return `${student.family_names}, ${student.given_names}`;
}

/** "Given Family" (the detail's heading). */
export function fullName(student: {
  given_names: string;
  family_names: string;
}): string {
  return `${student.given_names} ${student.family_names}`;
}
