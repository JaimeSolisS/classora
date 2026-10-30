import type {
  BillingSettings,
  CancellationRule,
  CancellationRuleOverview,
} from './api';

// Billing settings fixtures (#366 E1, E2) for the tests and the stories.
// The center's "today" in them is 2026-10-20.

export const SETTINGS_OFF: BillingSettings = {
  billing_starts_on: null,
  version: 1,
  updated_at: '2026-10-01T00:00:00Z',
};

export const SETTINGS_ON: BillingSettings = {
  billing_starts_on: '2026-09-01',
  version: 2,
  updated_at: '2026-10-02T00:00:00Z',
};

// Cancellation billing rules fixtures (#385 D1, D2). SEEDED is the
// migration's version (credit on time, charge late and no-show); WITH_UPCOMING
// adds a change ten years ahead (late credited) after an earlier version.

const rule = (
  id: string,
  effectiveFrom: string,
  outcomes: Partial<
    Pick<
      CancellationRule,
      'on_time_outcome' | 'late_outcome' | 'no_show_outcome' | 'excused_outcome'
    >
  > = {},
): CancellationRule => ({
  id,
  effective_from: effectiveFrom,
  created_at: effectiveFrom,
  created_by_user_id: null,
  excused_outcome: 'credit',
  on_time_outcome: 'credit',
  late_outcome: 'charge',
  no_show_outcome: 'charge',
  ...outcomes,
});

export const SEEDED_RULE = rule(
  '00000000-0000-4000-8000-0000000000c1',
  '2000-01-01T06:00:00Z',
);

export const RULES_SEEDED: CancellationRuleOverview = {
  current: SEEDED_RULE,
  upcoming: [],
  history: [],
};

/** In force since 2026-09-01 (no-show credited), after the seeded one. */
export const CURRENT_RULE = rule(
  '00000000-0000-4000-8000-0000000000c2',
  '2026-09-01T06:00:00Z',
  { no_show_outcome: 'credit' },
);

export const UPCOMING_RULE = rule(
  '00000000-0000-4000-8000-0000000000c3',
  '2036-10-21T06:00:00Z',
  { late_outcome: 'credit', no_show_outcome: 'credit' },
);

export const RULES_WITH_UPCOMING: CancellationRuleOverview = {
  current: CURRENT_RULE,
  upcoming: [UPCOMING_RULE],
  history: [SEEDED_RULE],
};
