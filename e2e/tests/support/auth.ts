import { fileURLToPath } from 'node:url';
import type { Page } from '@playwright/test';

// The shared signed-in state (#28 H1). e2e/global-setup.ts signs the demo
// Owner in once through the API and saves the browser state here
// (gitignored); every test starts from it unless it opts out. The shared
// session is never logged out.
export const OWNER_STATE_PATH = fileURLToPath(
  new URL('../../.auth/owner.json', import.meta.url),
);

/**
 * For tests that log in or out (#28 H2): a context WITHOUT the shared state.
 * A login sent with the shared cookie would revoke the shared session (#23
 * rotation), and a logout would end it.
 */
export const SIGNED_OUT_STATE = { cookies: [], origins: [] };

/**
 * Login budget (#27, ADR-0008): one run shares one `login.ip` budget of 50
 * per 15 minutes, and `demo.owner` has 10 failures per 15 minutes. Every
 * `make e2e` starts from an empty volume, so the budget is per run. Logins in
 * the whole run today: global setup 1, auth/session.spec.ts 8 (2 of them
 * wrong passwords), security/headers.spec.ts 1, auth/invitations.spec.ts 2,
 * auth/password-reset.spec.ts 8 (2 of them by createActiveUser),
 * auth/registration.spec.ts 2 (the first refused while the account is
 * pending: 1 failure on `e2e.parent`'s own `login.username` budget) = 22
 * attempts, 2 failures for `demo.owner`. Keep the total well under both
 * limits; never add a setting that disables the limits.
 *
 * Token redemption (#29, #30, ADR-0008 D15): `token_redemption.ip` allows 20
 * lookups and redemptions per 15 minutes for the run's one IP, invitations
 * and resets together. auth/invitations.spec.ts makes 3 (2 lookups, 1
 * redemption) and auth/password-reset.spec.ts 7 (4 of them by
 * createActiveUser) = 10.
 */
export const LOGIN_ATTEMPTS_PER_RUN = 22;

/** Invitation and reset lookups and redemptions in one run (see above). */
export const TOKEN_REDEMPTIONS_PER_RUN = 10;

/**
 * Self-registration requests in one run (#169; `registration.ip` allows 10
 * per hour for the run's one IP): auth/registration.spec.ts sends 1.
 */
export const REGISTRATIONS_PER_RUN = 1;

/** Records CSP violations from the first script on (the #24 listener). */
export async function recordCspViolations(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const target = window as Window & { __cspViolations?: string[] };
    target.__cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      target.__cspViolations?.push(
        `${event.violatedDirective} ${event.blockedURI}`,
      );
    });
  });
}

export async function cspViolations(page: Page): Promise<string[]> {
  return page.evaluate(
    () =>
      (window as Window & { __cspViolations?: string[] }).__cspViolations ?? [],
  );
}
