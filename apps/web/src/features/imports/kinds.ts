import type { AccessRequirement } from '@/features/auth/access';
import { canAccess } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';

// The import kinds of the web app (M8-05b B1, B2; the API registry is #122
// E5). Who may import a kind mirrors the API (UX only: the API checks every
// request): students need student.manage; tutors need user.invite and the
// authority to assign the tutor role (user.role.assign); recurring schedules
// need schedule.manage and opening balances billing.manage (#123 F1).

export type ImportKindCode =
  'students' | 'tutors' | 'schedules' | 'opening_balances';

export interface ImportColumn {
  name: string;
  required: boolean;
}

export interface ImportKindDefinition {
  code: ImportKindCode;
  access: AccessRequirement;
  /** The API's columns, in its order (`_docs/api.md` Imports). */
  columns: readonly ImportColumn[];
  /** The template's example row, one value per column (synthetic). */
  example: readonly string[];
}

const required = (name: string): ImportColumn => ({ name, required: true });
const optional = (name: string): ImportColumn => ({ name, required: false });

export const IMPORT_KINDS: readonly ImportKindDefinition[] = [
  {
    code: 'students',
    access: { permission: 'student.manage' },
    columns: [
      required('given_names'),
      required('family_names'),
      required('birthdate'),
      optional('email'),
      optional('phone'),
      optional('school_name'),
      optional('school_grade'),
      optional('education_level'),
      required('contact_name'),
      required('contact_relationship'),
      required('contact_phone'),
      optional('contact2_name'),
      optional('contact2_relationship'),
      optional('contact2_phone'),
    ],
    example: [
      'Ana Sofía',
      'Ejemplo Pérez',
      '2014-03-15',
      '',
      '',
      'Escuela Ejemplo',
      '4.º',
      'Primaria',
      'María Ejemplo',
      'Madre',
      '55 0000 0000',
      '',
      '',
      '',
    ],
  },
  {
    code: 'tutors',
    access: { permission: 'user.invite', anyPermission: ['user.role.assign'] },
    columns: [
      required('username'),
      required('display_name'),
      optional('email'),
      optional('phone'),
    ],
    example: ['tutor.ejemplo', 'Tutor Ejemplo', 'tutor@example.com', ''],
  },
  {
    code: 'schedules',
    access: { permission: 'schedule.manage' },
    columns: [
      required('student_given_names'),
      required('student_family_names'),
      required('student_birthdate'),
      required('subject'),
      required('education_level'),
      required('starts_on'),
      required('start_time'),
      required('duration_minutes'),
      required('weekdays'),
      optional('interval'),
      optional('until'),
      optional('count'),
      optional('tutor_username'),
      optional('classroom'),
    ],
    example: [
      'Ana Sofía',
      'Ejemplo Pérez',
      '2014-03-15',
      'Matemáticas',
      'Primaria',
      '2031-01-06',
      '16:00',
      '60',
      '1 3',
      '1',
      '',
      '24',
      'tutor.ejemplo',
      '',
    ],
  },
  {
    code: 'opening_balances',
    access: { permission: 'billing.manage' },
    columns: [
      required('student_given_names'),
      required('student_family_names'),
      required('student_birthdate'),
      required('description'),
      required('amount'),
      required('service_date'),
      required('due_date'),
    ],
    example: [
      'Ana Sofía',
      'Ejemplo Pérez',
      '2014-03-15',
      'Saldo inicial',
      '1250.00',
      '2031-01-01',
      '2031-01-15',
    ],
  },
];

export function isImportKind(value: string): value is ImportKindCode {
  return IMPORT_KINDS.some((kind) => kind.code === value);
}

export function importKind(code: string): ImportKindDefinition | undefined {
  return IMPORT_KINDS.find((kind) => kind.code === code);
}

/** The kinds `user` may import, in catalog order. */
export function availableKinds(
  user: SessionUser | null | undefined,
): ImportKindDefinition[] {
  return IMPORT_KINDS.filter((kind) => canAccess(user, kind.access));
}

/** One CSV cell (RFC 4180): quoted when it holds a comma, quote or newline. */
function cell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

/**
 * The template of a kind (E2): a UTF-8 BOM (so Excel opens it as UTF-8),
 * the header and one example row, CRLF line ends. Built in memory: no
 * stored file is served.
 */
export function templateCsv(kind: ImportKindDefinition): string {
  const lines = [
    kind.columns.map((column) => cell(column.name)).join(','),
    kind.example.map(cell).join(','),
  ];
  return `\uFEFF${lines.join('\r\n')}\r\n`;
}

/** The template's file name. */
export function templateFileName(kind: ImportKindDefinition): string {
  return `classora-${kind.code}-template.csv`;
}
