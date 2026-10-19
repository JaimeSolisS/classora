import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The password-reset call (#30). A direct call, never a TanStack mutation or
// query: its response holds the one-time link, which must never enter a
// cache (I5).

export type PasswordResetIssued = components['schemas']['PasswordResetIssued'];

/**
 * POST /password-resets. Always needs a recent reauthentication, so apiCall
 * opens the password dialog and retries once (#28 D).
 */
export function issuePasswordReset(
  username: string,
): Promise<PasswordResetIssued> {
  return apiCall(() =>
    api.POST('/api/v1/password-resets', { body: { username } }),
  );
}
