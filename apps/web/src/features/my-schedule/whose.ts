import type { TFunction } from 'i18next';
import type { FamilyStudent } from './api';

// "Whose schedule" (#67 E2): the entries a caller can choose and the one
// `?for=` selects. Pure.
//
// - A tutor gets "My teaching" (`for=teaching`).
// - Each family student of GET /schedule/family-students gets an entry
//   (`for=<student_id>`): "My classes" for `relation: self`, the child's
//   name for `guardian`.
//
// Default: "My teaching" for a tutor, otherwise the first family student.
// An unknown or foreign `?for=` falls back to the default (no request is
// made for it).

export const TEACHING = 'teaching';

export type WhoseEntry =
  | { kind: 'teaching'; key: typeof TEACHING }
  | {
      kind: 'student';
      key: string;
      student: FamilyStudent;
    };

export function whoseEntries(
  isTutor: boolean,
  students: readonly FamilyStudent[],
): WhoseEntry[] {
  return [
    ...(isTutor ? [{ kind: 'teaching', key: TEACHING } as const] : []),
    ...students.map((student): WhoseEntry => ({
      kind: 'student',
      key: student.student_id,
      student,
    })),
  ];
}

/** The entry `requested` names, or the default; null when there is none. */
export function resolveWhose(
  entries: readonly WhoseEntry[],
  requested: string | null,
): WhoseEntry | null {
  const wanted =
    requested === null
      ? undefined
      : entries.find((entry) => entry.key === requested.toLowerCase());
  return wanted ?? entries[0] ?? null;
}

/** An entry's label: "My teaching", "My classes" or the child's name. */
export function entryLabel(entry: WhoseEntry, t: TFunction<'common'>): string {
  if (entry.kind === 'teaching') {
    return t('mySchedule.whose.teaching');
  }
  return entry.student.relation === 'self'
    ? t('mySchedule.whose.self')
    : entry.student.display_name;
}
