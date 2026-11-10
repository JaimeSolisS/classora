import type { SessionUser } from '@/features/auth/session';
import type { LocalDate } from '@/lib/time';
import {
  FamilyAgendaSection,
  StaffAgendaSection,
  TutorAgendaSection,
} from './Agenda';
import type { HeadingLevel } from './Card';
import { PendingSection } from './Pending';
import { homePlan, personalWindow, staffWindow } from './rules';

// The sections Home shows a user (HF-06 E6): the union of what the roles
// allow, in a fixed order — the center's classes today (staff), "My
// classes" (tutor), the family's classes (student or guardian), then the
// pending items as one list. Used by the role home (h2 sections) and by the
// dashboard's "Today" panel (h3 sections).

export interface HomeContentProps {
  user: SessionUser;
  timeZone: string;
  today: LocalDate;
  /** The clock (the "to close out" count); tests and stories pass it. */
  now: number;
  level: HeadingLevel;
}

export function HomeContent({
  user,
  timeZone,
  today,
  now,
  level,
}: HomeContentProps) {
  const plan = homePlan(user);
  const staff = staffWindow(today, timeZone);
  const personal = personalWindow(today, timeZone);
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-2" data-home-sections>
      {plan.staff ? (
        <StaffAgendaSection
          today={today}
          timeZone={timeZone}
          window={staff}
          level={level}
        />
      ) : null}
      {plan.tutor ? (
        <TutorAgendaSection
          userId={user.id}
          userName={user.display_name}
          today={today}
          timeZone={timeZone}
          window={personal}
          level={level}
        />
      ) : null}
      {plan.family ? (
        <FamilyAgendaSection
          today={today}
          timeZone={timeZone}
          window={personal}
          level={level}
        />
      ) : null}
      {plan.pending.length > 0 ? (
        <PendingSection
          codes={plan.pending}
          userId={user.id}
          today={today}
          timeZone={timeZone}
          staffWindow={staff}
          personalWindow={personal}
          now={now}
          level={level}
        />
      ) : null}
    </div>
  );
}
