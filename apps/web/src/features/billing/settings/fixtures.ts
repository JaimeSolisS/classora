import type { BillingSettings } from './api';

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
