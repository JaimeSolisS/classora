import { GraduationCap, User, Users, type LucideIcon } from 'lucide-react';
import type { CalendarItem } from './api';

// The class types of a session (HF-07 E1): their codes, icons and colours
// (tokens.css). The components that show them are in KindBadge.tsx.

export type SessionKind = CalendarItem['kind'];

export const SESSION_KINDS = [
  'individual',
  'group',
  'course',
] as const satisfies readonly SessionKind[];

export const KIND_ICONS: Record<SessionKind, LucideIcon> = {
  individual: User,
  group: Users,
  course: GraduationCap,
};

/** The event background of a class type (a CSS value). */
export function kindTint(kind: SessionKind): string {
  return `var(--kind-${kind})`;
}

/** The swatch / List-dot colour of a class type (a CSS value). */
export function kindStrong(kind: SessionKind): string {
  return `var(--kind-${kind}-strong)`;
}
