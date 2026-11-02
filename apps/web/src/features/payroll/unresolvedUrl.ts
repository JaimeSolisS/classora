import type { UnresolvedFilters, UnresolvedReason } from './api';

// The unresolved work's filters in the URL (#444 C1): `tutor` (a UUID) and
// `reason`. Anything invalid is ignored, so a bad link never reaches the API
// as a 422. Pure.

export const UNRESOLVED_REASONS: readonly UnresolvedReason[] = [
  'no_pay_terms',
  'no_rate',
  'before_first_period',
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function parseUnresolvedFilters(
  params: URLSearchParams,
): UnresolvedFilters {
  const tutor = params.get('tutor');
  const reason = params.get('reason');
  return {
    tutor: tutor !== null && UUID.test(tutor) ? tutor.toLowerCase() : null,
    reason: UNRESOLVED_REASONS.find((item) => item === reason) ?? null,
  };
}

export function unresolvedSearchParams({
  tutor,
  reason,
}: UnresolvedFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (tutor) params.set('tutor', tutor);
  if (reason) params.set('reason', reason);
  return params;
}
