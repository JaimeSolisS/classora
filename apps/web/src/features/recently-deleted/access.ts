import type { AccessRequirement } from '@/features/auth/access';

// Who opens /recently-deleted (#129 D1): the users who can delete an
// eligible record — tutor-role users (notes, evaluation drafts, plans,
// homework, progress reports) and learning_plan.manage holders (plan
// drafts). Kept apart from the page so the router and the navigation don't
// load it. UX only: the API lists only the caller's own records.
export const RECENTLY_DELETED_ACCESS = {
  anyOf: [{ role: 'tutor' }, { permission: 'learning_plan.manage' }],
} as const satisfies AccessRequirement;
