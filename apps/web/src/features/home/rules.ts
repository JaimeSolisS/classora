import { NAV_ITEMS } from '@/app/navigation';
import type { AccessRequirement } from '@/features/auth/access';
import { canAccess } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';
import { APPROVAL_REVIEW_PERMISSIONS } from '@/features/approvals/api';
import type { CalendarItem } from '@/features/calendar/api';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import { FAMILY_BILLING_ACCESS } from '@/features/family-portal/billing/access';
import {
  addDays,
  localDayBounds,
  toLocalDate,
  type Instant,
  type LocalDate,
} from '@/lib/time';

// The home page's rules (HF-06), pure: who gets which section and which
// pending item, the agenda windows and the "today" split, all in the
// center time zone (never the browser's). UX only: the API checks every
// request; these gates only keep Home from asking what a role is refused.

export const STAFF_AGENDA_ACCESS = {
  permission: 'schedule.view',
} as const satisfies AccessRequirement;
export const TUTOR_AGENDA_ACCESS = {
  role: 'tutor',
} as const satisfies AccessRequirement;
export const FAMILY_AGENDA_ACCESS = {
  anyRole: ['student', 'guardian'],
} as const satisfies AccessRequirement;

/** The pending items (C1), in display order. */
export type PendingCode =
  | 'approvals'
  | 'overdueCloseouts'
  | 'overdueAccounts'
  | 'attention'
  | 'offers'
  | 'toCloseOut'
  | 'toGrade'
  | 'homeworkPending'
  | 'homeworkMissing'
  | 'balance';

export const PENDING_ACCESS: Readonly<Record<PendingCode, AccessRequirement>> =
  {
    approvals: { anyPermission: APPROVAL_REVIEW_PERMISSIONS },
    overdueCloseouts: { permission: 'teaching_hours.approve' },
    overdueAccounts: { permission: 'billing.view' },
    attention: STAFF_AGENDA_ACCESS,
    offers: TUTOR_AGENDA_ACCESS,
    toCloseOut: TUTOR_AGENDA_ACCESS,
    toGrade: TUTOR_AGENDA_ACCESS,
    homeworkPending: FAMILY_AGENDA_ACCESS,
    homeworkMissing: FAMILY_AGENDA_ACCESS,
    balance: FAMILY_BILLING_ACCESS,
  };

export const PENDING_CODES = Object.keys(PENDING_ACCESS) as PendingCode[];

/** What Home shows a user: the agenda sections and the pending items. */
export interface HomePlan {
  staff: boolean;
  tutor: boolean;
  family: boolean;
  pending: readonly PendingCode[];
}

export function homePlan(user: SessionUser | null | undefined): HomePlan {
  return {
    staff: canAccess(user, STAFF_AGENDA_ACCESS),
    tutor: canAccess(user, TUTOR_AGENDA_ACCESS),
    family: canAccess(user, FAMILY_AGENDA_ACCESS),
    pending: PENDING_CODES.filter((code) =>
      canAccess(user, PENDING_ACCESS[code]),
    ),
  };
}

/** No agenda section and no pending source: the fallback (D3). */
export function isEmptyPlan(plan: HomePlan): boolean {
  return (
    !plan.staff && !plan.tutor && !plan.family && plan.pending.length === 0
  );
}

export interface Window {
  from: Instant;
  to: Instant;
}

/** Staff (B1): [start of today, +7 days) in the center zone. */
export const STAFF_DAYS = 7;
/** Tutor and family (B2, B3): [today − 14 days, today + 14 days). */
export const PERSONAL_DAYS = 14;
/** Today's staff rows shown before "and n more" (B1). */
export const STAFF_TODAY_ROWS = 8;
/** Upcoming rows after today (B2). */
export const UPCOMING_ROWS = 5;
/** The most students whose schedule Home reads (B3). */
export const MAX_FAMILY_STUDENTS = 5;

export function staffWindow(today: LocalDate, timeZone: string): Window {
  return {
    from: localDayBounds(today, timeZone).start,
    to: localDayBounds(addDays(today, STAFF_DAYS), timeZone).start,
  };
}

export function personalWindow(today: LocalDate, timeZone: string): Window {
  return {
    from: localDayBounds(addDays(today, -PERSONAL_DAYS), timeZone).start,
    to: localDayBounds(addDays(today, PERSONAL_DAYS), timeZone).start,
  };
}

function byStart<T extends { starts_at: Instant }>(a: T, b: T): number {
  return Date.parse(a.starts_at) - Date.parse(b.starts_at);
}

export interface StaffAgenda {
  today: CalendarItem[];
  /** The first session after today, if any (the empty state, B4). */
  next: CalendarItem | null;
  /** Sessions without a tutor or in conflict over the whole window. */
  attention: number;
}

export function staffAgenda(
  items: readonly CalendarItem[],
  today: LocalDate,
  timeZone: string,
): StaffAgenda {
  const sorted = items
    .filter((item) => item.state !== 'cancelled')
    .sort(byStart);
  return {
    today: sorted.filter(
      (item) => toLocalDate(item.starts_at, timeZone) === today,
    ),
    next:
      sorted.find((item) => toLocalDate(item.starts_at, timeZone) > today) ??
      null,
    attention: sorted.filter(
      (item) => item.state === 'unassigned' || item.state === 'conflict',
    ).length,
  };
}

/** One row of a personal agenda: the entry and, for a family, whose. */
export interface AgendaRow {
  entry: ScheduleEntry;
  /** The family student the row is for (null: the tutor's teaching). */
  studentId: string | null;
  /** The student's name, when the agenda merges several students. */
  studentName: string | null;
}

export interface PersonalAgenda {
  today: AgendaRow[];
  upcoming: AgendaRow[];
}

const HIDDEN_STATES: ReadonlySet<string> = new Set([
  'cancelled',
  'skipped_closure',
]);

/** Today's rows, then up to UPCOMING_ROWS after today (B2, B3). */
export function personalAgenda(
  rows: readonly AgendaRow[],
  today: LocalDate,
  timeZone: string,
): PersonalAgenda {
  const shown = rows
    .filter((row) => !HIDDEN_STATES.has(row.entry.item.state))
    .sort((a, b) => byStart(a.entry.item, b.entry.item));
  return {
    today: shown.filter(
      (row) => toLocalDate(row.entry.item.starts_at, timeZone) === today,
    ),
    upcoming: shown
      .filter((row) => toLocalDate(row.entry.item.starts_at, timeZone) > today)
      .slice(0, UPCOMING_ROWS),
  };
}

/** The tutor's confirmed sessions that have ended: to close out (C1). */
export function toCloseOut(
  entries: readonly ScheduleEntry[],
  now: number,
): ScheduleEntry[] {
  return entries.filter(
    (entry) =>
      entry.audience === 'tutor' &&
      entry.item.state === 'confirmed' &&
      Date.parse(entry.item.ends_at) <= now,
  );
}

/** The fallback's quick links (D3): at most this many. */
export const QUICK_LINKS_MAX = 8;

/** The user's visible navigation items except Home, in nav order. */
export function quickLinks(user: SessionUser | null) {
  return NAV_ITEMS.filter(
    (item) => item.to !== '/' && canAccess(user, item),
  ).slice(0, QUICK_LINKS_MAX);
}
