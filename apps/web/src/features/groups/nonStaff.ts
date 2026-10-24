import type { TFunction } from 'i18next';
import type { SessionState } from '@/features/calendar/api';
import { toLocalDate } from '@/lib/time';

// What the group and cohort pages show tutors, students and guardians
// (#246 B3, B4), pure. They can't open the staff session page, so a session
// links to its day in "My schedule" (List view); a state reads with the
// "My schedule" wording (#67 E6: "Cancelled", "Not held: center closed");
// and a guardian with more than one child in the list sees whose session
// each one is.

/** `/my-schedule?date=<local date>&view=list` for a session (B4). */
export function myScheduleHref(startsAt: string, timeZone: string): string {
  const params = new URLSearchParams({
    date: toLocalDate(startsAt, timeZone),
    view: 'list',
  });
  return `/my-schedule?${params.toString()}`;
}

const FAMILY_STATES = [
  'offered',
  'confirmed',
  'completed',
  'cancelled',
  'skipped_closure',
] as const;

type FamilyState = (typeof FAMILY_STATES)[number];

function isFamilyState(state: SessionState): state is FamilyState {
  return (FAMILY_STATES as readonly string[]).includes(state);
}

/**
 * The state as text: the "My schedule" wording for a tutor, a student or a
 * guardian (the API sends them only these states), the staff wording
 * otherwise.
 */
export function sessionStateText(
  state: SessionState,
  t: TFunction<'common'>,
  staff: boolean,
): string {
  if (!staff && isFamilyState(state)) {
    return t(`mySchedule.states.${state}`);
  }
  return t(`scheduling.states.${state}`);
}

interface WithStudents {
  students?: readonly { id: string; display_name: string }[] | null;
}

/**
 * Whether the items name their students (B3): more than one distinct
 * student across the list (a guardian with several children in it).
 */
export function showsStudentNames(items: readonly WithStudents[]): boolean {
  const ids = new Set<string>();
  for (const item of items) {
    for (const student of item.students ?? []) {
      ids.add(student.id);
    }
  }
  return ids.size > 1;
}

/** The names, in the API's order (by name), joined. */
export function namesText(
  people: readonly { display_name: string }[] | null | undefined,
): string {
  return (people ?? []).map((person) => person.display_name).join(', ');
}
