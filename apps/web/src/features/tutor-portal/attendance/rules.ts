import type {
  AttendanceItem,
  AttendanceMark,
  AttendanceStatus,
  SessionAttendance,
} from './api';

// The attendance page's pure rules (#76 G1, G2): the choices a tutor
// starts from, the marks a save sends (only new or changed ones), the
// latest correction of a row, and when My schedule offers attendance.

/** The status chosen per student (`null`: not chosen yet). */
export type Choices = Readonly<Record<string, AttendanceStatus | null>>;

export function initialChoices(attendance: SessionAttendance): Choices {
  return Object.fromEntries(
    attendance.items.map((item) => [item.student.id, item.status]),
  );
}

/**
 * The marks to send: chosen, different from the saved status, and not on
 * a corrected row (read-only for the tutor), in the list's order.
 */
export function changedMarks(
  attendance: SessionAttendance,
  choices: Choices,
): AttendanceMark[] {
  const marks: AttendanceMark[] = [];
  for (const item of attendance.items) {
    const chosen = choices[item.student.id] ?? null;
    if (!item.corrected && chosen !== null && chosen !== item.status) {
      marks.push({ student_id: item.student.id, status: chosen });
    }
  }
  return marks;
}

/** The newest correction of a row, or `undefined`. */
export function latestCorrection(item: AttendanceItem) {
  return item.corrections[item.corrections.length - 1];
}

/** G2: a tutor's entry links to attendance once it can be taken or read. */
export type AttendanceLink = 'take' | 'view' | null;

export function tutorAttendanceLink(
  item: { state: string; starts_at: string },
  now: number,
): AttendanceLink {
  if (item.state === 'completed') {
    return 'view';
  }
  if (item.state === 'confirmed' && Date.parse(item.starts_at) <= now) {
    return 'take';
  }
  return null;
}

/** G2: a family entry shows its attendance once the class has started. */
export function showsFamilyAttendance(
  item: { state: string; starts_at: string },
  now: number,
): boolean {
  return (
    (item.state === 'confirmed' || item.state === 'completed') &&
    Date.parse(item.starts_at) <= now
  );
}
